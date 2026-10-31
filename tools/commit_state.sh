#!/usr/bin/env bash
# Çalışma durumunu (state/*.db, strateji, panel) depoya geri yazar. Yarış durumlarında yeniden dener.
set -u
label="${1:-state}"
git config user.name "forge-bot"
git config user.email "forge-bot@users.noreply.github.com"
git add state docs 2>/dev/null
if git diff --cached --quiet; then
  echo "Kaydedilecek değişiklik yok"
  exit 0
fi
git commit -q -m "state: ${label} $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3 4 5 6; do
  if git pull --rebase -X theirs -q origin "${GITHUB_REF_NAME:-main}" && git push -q origin "HEAD:${GITHUB_REF_NAME:-main}"; then
    echo "Durum kaydedildi"
    exit 0
  fi
  git rebase --abort 2>/dev/null
  sleep $((RANDOM % 10 + 4))
done
echo "::error::Durum kaydedilemedi (push başarısız)"
exit 1
