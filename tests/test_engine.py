"""Mantık testleri (FFmpeg/Pillow/ağ gerektirmez). Çalıştır: python -m unittest discover -s tests -v"""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.update({"FORGE_MOCK": "1", "FORGE_SKIP_RENDER": "1"})

from forge import budget, config, dashboard, quality, research, strategy, tts  # noqa: E402
from forge.db import DB, now  # noqa: E402
from forge.pipelines import common, long_video, short_video, tweets  # noqa: E402
from forge.publish import x  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._patches = [
            mock.patch.object(config, "STATE_DIR", self.tmp / "state"),
            mock.patch.object(config, "OUT_DIR", self.tmp / "out"),
            mock.patch.object(config, "DOCS_DIR", self.tmp / "docs"),
            mock.patch.object(config, "CONTROL_FILE", self.tmp / "state" / "control.json"),
        ]
        for p in self._patches:
            p.start()
        config.STATE_DIR.mkdir(parents=True)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def db(self, ch):
        return DB(ch)


class TestQuality(unittest.TestCase):
    def test_overlap_detects_copy(self):
        a = "bu bir deneme metnidir ve oldukça uzun bir cümle içerir test için"
        self.assertGreater(quality.overlap(a, a), 0.9)
        self.assertLess(quality.overlap(a, "tamamen farklı kelimelerle yazılmış başka bir paragraf burada"), 0.1)

    def test_unverified_numbers_penalized(self):
        r = quality.check_script("Gemi 24000 konteyner taşır.", [{"text": "Gemi 24000 konteyner taşır.", "visual_query": "ship"}], [],
                                 source_text="kaynakta yalnızca 12000 yazıyor")
        self.assertLess(r.score, 100)
        ok = quality.check_script("Gemi 24000 konteyner taşır.", [{"text": "Gemi 24000 konteyner taşır.", "visual_query": "ship"}], [],
                                  source_text="kaynakta 24.000 konteyner yazıyor")
        self.assertEqual(ok.score, 100)

    def test_high_overlap_is_critical(self):
        t = " ".join(f"kelime{i}" for i in range(40))
        r = quality.check_script(t, [{"text": t, "visual_query": "x"}], [t])
        self.assertTrue(r.critical)
        self.assertFalse(r.passed(10))

    def test_policy_keyword_is_critical(self):
        r = quality.check_script("bomba yapımı anlatılıyor", [{"text": "bomba yapımı", "visual_query": "x"}], [])
        self.assertTrue(r.critical)


class TestX(unittest.TestCase):
    def test_weighted_len(self):
        self.assertEqual(x.weighted_len("a" * 10), 10)
        self.assertEqual(x.weighted_len("bak https://example.com/çok/uzun/bir/adres"), 4 + 23)

    def test_oauth_header_shape(self):
        keys = {"X_API_KEY": "k", "X_API_SECRET": "s", "X_ACCESS_TOKEN": "t", "X_ACCESS_SECRET": "ts"}
        h = x.oauth1_header("POST", "https://api.x.com/2/tweets", keys)
        for part in ("oauth_consumer_key", "oauth_signature", "oauth_nonce", "oauth_token"):
            self.assertIn(part, h)

    def test_oauth_signature_known_vector(self):
        # RFC 5849 / Twitter dokümanındaki örnek imza hesaplaması (sabit nonce/timestamp ile)
        keys = {"X_API_KEY": "xvz1evFS4wEEPTGEFPHBog", "X_API_SECRET": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
                "X_ACCESS_TOKEN": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
                "X_ACCESS_SECRET": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"}
        with mock.patch.object(x.secrets, "token_hex", return_value="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg"), \
             mock.patch.object(x.time, "time", return_value=1318622958):
            h = x.oauth1_header("POST", "https://api.twitter.com/1.1/statuses/update.json", keys,
                                {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"})
        self.assertIn("hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D", h)


class TestBudget(Base):
    def test_zero_budget_blocks_paid(self):
        db = self.db("ig_facts")
        budget.guard(db, 0)  # ücretsiz işlem geçer
        with self.assertRaises(budget.BudgetExceeded):
            budget.guard(db, 0.5)
        config.CONTROL_FILE.write_text(json.dumps({"channels": {"ig_facts": {"budget_usd": 5}}}))
        budget.guard(db, 0.5)
        db.add_cost("tts", 4.9)
        with self.assertRaises(budget.BudgetExceeded):
            budget.guard(db, 0.5)
        db.close()


class TestControl(Base):
    def test_pause_stops_pipeline(self):
        config.CONTROL_FILE.write_text(json.dumps({"paused": True}))
        self.assertFalse(short_video.run("ig_facts"))
        self.assertEqual(tweets.plan("x_tech"), 0)


class TestShort(Base):
    def test_end_to_end_and_daily_limit(self):
        self.assertTrue(short_video.run("ig_facts"))
        db = self.db("ig_facts")
        items = db.items(kind="short")
        self.assertEqual(items[0]["status"], "ready")  # mock = kuru çalıştırma, yayınlamaz
        self.assertGreaterEqual(items[0]["quality"], 75)
        self.assertTrue(items[0]["payload"]["script"]["scenes"])
        self.assertEqual(len(db.posts()), 0)
        db.close()
        for _ in range(7):
            short_video.run("ig_facts")
        db = self.db("ig_facts")
        self.assertEqual(db.count_since("short", common.day_start_ts()), config.CHANNELS["ig_facts"]["daily"])
        db.close()

    def test_failure_is_recorded_and_retried(self):
        with mock.patch.object(tts, "synthesize", side_effect=RuntimeError("tts down")):
            self.assertFalse(short_video.run("ig_facts"))
        db = self.db("ig_facts")
        it = db.items(kind="short")[0]
        self.assertEqual(it["attempts"], 1)
        self.assertIn("tts down", it["error"])
        # senaryo kaydedildiği için iş "new" olarak kalır ve sonraki çalışmada devam eder
        self.assertEqual(it["status"], "new")
        db.close()
        self.assertTrue(short_video.run("ig_facts"))
        db = self.db("ig_facts")
        self.assertEqual(db.get_item(it["id"])["status"], "ready")
        db.close()

    def test_gives_up_after_max_attempts(self):
        with mock.patch.object(tts, "synthesize", side_effect=RuntimeError("down")):
            for _ in range(common.MAX_ATTEMPTS):
                short_video.run("ig_facts")
        db = self.db("ig_facts")
        self.assertEqual(db.items(kind="short")[0]["status"], "failed")
        db.close()


class TestLong(Base):
    def test_documentary_end_to_end(self):
        self.assertTrue(long_video.run("yt_doc"))
        db = self.db("yt_doc")
        it = db.items(kind="long")[0]
        self.assertEqual(it["status"], "ready")
        n_src = db.conn.execute("SELECT COUNT(*) n FROM sources WHERE item_id=?", (it["id"],)).fetchone()["n"]
        self.assertGreaterEqual(n_src, 5)
        self.assertGreater(db.conn.execute("SELECT COUNT(*) n FROM claims WHERE item_id=?", (it["id"],)).fetchone()["n"], 5)
        self.assertIn("opportunity", it["payload"]["candidate"])
        self.assertGreaterEqual(it["quality"], 82)
        minutes = it["payload"]["duration"] / 60
        self.assertGreater(minutes, 10)
        self.assertIn("Kaynaklar:", it["payload"]["description"])
        self.assertIn("0:00", it["payload"]["description"])
        db.close()

    def test_weekly_limit(self):
        for _ in range(4):
            long_video.run("yt_doc")
        db = self.db("yt_doc")
        self.assertEqual(db.count_since("long", common.week_start_ts()), 2)
        db.close()

    def test_topic_rejected_when_sources_insufficient(self):
        bad = {"sources": [], "independent": 1, "ok": False, "reasons": ["yetersiz bağımsız kaynak"]}
        with mock.patch.object(research, "gather", return_value=bad):
            self.assertFalse(long_video.run("yt_doc"))
        db = self.db("yt_doc")
        self.assertGreaterEqual(len(db.items(status="rejected")), 1)
        self.assertEqual(db.items(status="ready"), [])
        db.close()

    def test_fun_channel(self):
        self.assertTrue(long_video.run("yt_fun"))

    def test_opportunity_score_reports_unmeasured(self):
        s = long_video.score_topic({"topic": "x", "longevity": 80, "story": 80, "visual": 80, "advertiser": 80, "fit": 80}, [], None)
        self.assertEqual(s["measured"], [])
        self.assertLess(s["coverage"], 1)
        s2 = long_video.score_topic({"topic": "liman", "longevity": 80, "story": 80, "visual": 80, "advertiser": 80, "fit": 80},
                                    ["liman"], {"median_views": 500000, "n": 10, "big": 1})
        self.assertEqual(set(s2["measured"]), {"demand", "search", "competition"})
        self.assertEqual(s2["coverage"], 1.0)

    def test_publish_slot_skips_taken(self):
        db = self.db("yt_doc")
        s1 = common.next_publish_slot(db, hour_utc=16, weekdays=[1, 4])
        db.add_post(item_id=1, platform="youtube", external_id="a", url="", title="t", kind="long", scheduled_ts=s1, status="scheduled")
        s2 = common.next_publish_slot(db, hour_utc=16, weekdays=[1, 4])
        self.assertGreater(s2, s1)
        self.assertIn(time.gmtime(s2).tm_wday, (1, 4))
        db.close()


class TestTweets(Base):
    def test_plan_queue_and_post(self):
        n = tweets.plan("x_tech")
        self.assertGreater(n, 0)
        db = self.db("x_tech")
        queued = db.items(status="queued", kind="tweet")
        self.assertEqual(len(queued), n)
        for q in queued:
            self.assertLessEqual(x.weighted_len(q["payload"]["text"]), 280)
        dues = sorted(q["due_ts"] for q in queued)
        self.assertEqual(dues, sorted(set(dues)))  # aynı dakikada yığılma yok
        for q in queued[:2]:
            db.update_item(q["id"], due_ts=now() - 60)
        db.close()
        tweets.post_due("x_tech")  # mock → kuru çalıştırma
        db = self.db("x_tech")
        self.assertEqual(len(db.items(status="ready", kind="tweet")), 2)
        db.close()

    def test_plan_is_idempotent_per_day(self):
        n = tweets.plan("x_tech")
        self.assertEqual(n, config.CHANNELS["x_tech"]["daily"])
        self.assertEqual(tweets.plan("x_tech"), 0)

    def test_validation_rules(self):
        v = tweets._validate
        self.assertIsNone(v({"type": "take", "text": "merhaba dünya"}, [], set()))
        self.assertIsNotNone(v({"type": "take", "text": "@biri bak"}, [], set()))
        self.assertIsNotNone(v({"type": "take", "text": "a" * 300}, [], set()))
        self.assertIsNotNone(v({"type": "take", "text": "bak https://kotu.example"}, [], set()))
        self.assertIsNone(v({"type": "take", "text": "bak https://iyi.example"}, [], {"https://iyi.example"}))
        self.assertIsNotNone(v({"type": "take", "text": "#a #b #c"}, [], set()))
        self.assertIsNotNone(v({"type": "poll", "text": "q", "poll": {"options": ["tek"]}}, [], set()))

    def test_respects_monthly_limit(self):
        db = self.db("x_tech")
        db.quota_add("x_posts", time.strftime("%Y-%m", time.gmtime()), 500)
        db.close()
        self.assertEqual(tweets.plan("x_tech"), 0)


class TestStrategyAndDashboard(Base):
    def test_strategy_waits_for_data(self):
        short_video.run("ig_facts")
        self.assertFalse(strategy.update("ig_facts"))
        self.assertFalse(common.strategy_path("ig_facts").exists())

    def test_strategy_learns_from_real_metrics(self):
        db = self.db("ig_facts")
        for i in range(10):
            pid = db.add_post(item_id=i, platform="instagram", external_id=str(i), url="", title=f"video {i}", kind="short",
                              published_ts=now() - 5 * 86400)
            db.add_metric(pid, {"views": i * 100, "likes": i})
        db.close()
        self.assertTrue(strategy.update("ig_facts"))
        self.assertIn("kısa başlıklar", common.strategy_hints("ig_facts"))

    def test_dashboard_renders(self):
        short_video.run("ig_facts")
        long_video.run("yt_doc")
        path = pathlib.Path(dashboard.build())
        html = path.read_text(encoding="utf-8")
        self.assertIn("Dünya Nasıl Çalışıyor?", html)
        self.assertIn("Bunu Biliyor muydun?", html)
        self.assertNotIn("<script", html)  # manuel eylem/JS yok
        self.assertTrue((config.DOCS_DIR / "data.json").exists())


if __name__ == "__main__":
    unittest.main()
