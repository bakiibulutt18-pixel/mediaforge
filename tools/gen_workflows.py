"""Workflow dosyalarını üretir: python tools/gen_workflows.py  (zamanlamayı değiştirmek için buradan düzenle)."""
import pathlib

root = pathlib.Path(__file__).resolve().parent.parent
wf = root / ".github" / "workflows"
act = root / ".github" / "actions" / "setup"
wf.mkdir(parents=True, exist_ok=True)
act.mkdir(parents=True, exist_ok=True)

SECRETS = ["GEMINI_API_KEY", "GROQ_API_KEY", "PEXELS_API_KEY", "YOUTUBE_API_KEY",
           "YT_FUN_CLIENT_ID", "YT_FUN_CLIENT_SECRET", "YT_FUN_REFRESH_TOKEN",
           "YT_DOC_CLIENT_ID", "YT_DOC_CLIENT_SECRET", "YT_DOC_REFRESH_TOKEN",
           "IG_ACCESS_TOKEN", "IG_USER_ID",
           "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]

env_block = "\n".join(f"      {s}: ${{{{ secrets.{s} }}}}" for s in SECRETS)

(act / "action.yml").write_text("""name: setup
description: Python + render bağımlılıkları
inputs:
  render:
    description: "FFmpeg/Pillow/edge-tts gerekli mi"
    default: "false"
runs:
  using: composite
  steps:
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - if: inputs.render == 'true'
      shell: bash
      run: |
        sudo apt-get update -qq
        sudo apt-get install -y -qq --no-install-recommends fonts-dejavu-core espeak-ng
        pip install -r requirements.txt
""", encoding="utf-8")


def workflow(fname, name, crons, cmd, *, render, timeout, group, label, artifact=False,
             art_paths="out/**/final.mp4\n            out/**/thumb_*.jpg"):
    if crons:
        on = "on:\n  schedule:\n" + "\n".join(f"    - cron: '{c}'" for c in crons) + "\n  workflow_dispatch:"
    else:
        on = "on:\n  workflow_dispatch:"
    art = ""
    if artifact:
        art = f"""      - name: Önizleme çıktısı (yayın kapalıyken)
        if: ${{{{ always() && (vars.FORGE_LIVE != '1' || inputs.dry_run == true{" || true" if label == "demo" else ""}) }}}}
        uses: actions/upload-artifact@v4
        with:
          name: onizleme-{label}
          path: |
            {art_paths}
          retention-days: 3
          if-no-files-found: ignore
"""
    text = f"""name: {name}
{on}
    inputs:
      dry_run:
        description: "Üret ama yayınlama"
        type: boolean
        default: false
permissions:
  contents: write
concurrency:
  group: forge-{group}
  cancel-in-progress: false
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: {timeout}
    env:
      FORGE_LIVE: ${{{{ vars.FORGE_LIVE }}}}
      FORGE_DRY_RUN: ${{{{ inputs.dry_run == true && '1' || '' }}}}
      PYTHONUNBUFFERED: "1"
      TTS_PROVIDERS: ${{{{ vars.TTS_PROVIDERS }}}}
{env_block}
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup
        with:
          render: "{'true' if render else 'false'}"
      - name: Çalıştır
        run: python -m forge {cmd}
      - name: Paneli güncelle
        if: always()
        run: python -m forge dashboard
{art}      - name: Durumu kaydet
        if: always()
        run: bash tools/commit_state.sh "{label}"
"""
    (wf / fname).write_text(text, encoding="utf-8")


# 1) Instagram: günde 5 Reels (TR 09:30, 12:30, 15:30, 19:00, 21:30)
workflow("ig-reels.yml", "Instagram Reels", ["30 6 * * *", "30 9 * * *", "30 12 * * *", "0 16 * * *", "30 18 * * *"],
         "short ig_facts", render=True, timeout=30, group="ig_facts", label="ig_facts", artifact=True)
# 2) X: günlük plan + 30 dakikada bir yayın (TR 07:00-24:00)
workflow("x-plan.yml", "X günlük plan", ["30 3 * * *"], "tweets-plan x_tech", render=False, timeout=15, group="x_tech", label="x_plan")
workflow("x-post.yml", "X gönderi yayını", ["5,35 4-20 * * *"], "tweets-post x_tech", render=False, timeout=8, group="x_tech", label="x_post")
# 3) YouTube eğlence: günlük uzun video + günde 4 Shorts
workflow("yt-fun-long.yml", "YouTube eğlence - uzun video", ["0 5 * * *"], "long yt_fun", render=True, timeout=150,
         group="yt_fun", label="yt_fun", artifact=True)
workflow("yt-fun-shorts.yml", "YouTube eğlence - Shorts", ["0 7 * * *", "0 10 * * *", "0 14 * * *", "30 17 * * *"],
         "short yt_fun_shorts", render=True, timeout=30, group="yt_fun_shorts", label="yt_fun_shorts", artifact=True)
# 4) YouTube belgesel: haftada 2 video (Pzt/Sal/Çar üretim denemesi; haftalık hedef 2'de durur)
workflow("yt-doc.yml", "YouTube belgesel", ["0 5 * * 1,2,3"], "long yt_doc", render=True, timeout=200,
         group="yt_doc", label="yt_doc", artifact=True)
# Analitik (günlük), strateji (haftalık), sağlık kontrolü (elle)
workflow("analytics.yml", "Analitik", ["30 21 * * *"], "analytics all", render=False, timeout=20, group="analytics", label="analytics")
workflow("strategy.yml", "Haftalık strateji", ["0 22 * * 0"], "strategy all", render=False, timeout=20, group="analytics", label="strategy")
workflow("health.yml", "Sağlık kontrolü", [], "health", render=False, timeout=10, group="health", label="health")
# Ses + görsel kalite örnekleri (elle): yayın yapmaz, sonuçlar Artifacts'ta
workflow("demo.yml", "Kalite örnekleri", [], "demo-voice\n      - name: Örnek videolar\n        run: python -m forge demo-video",
         render=True, timeout=30, group="demo", label="demo", artifact=True, art_paths="out/demo/*.mp3\n            out/demo/ornek_*.mp4")
print("workflows:", sorted(f.name for f in wf.iterdir()))
