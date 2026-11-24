#!/usr/bin/env bash
# Roda NO EC2 (chamado pelo GitHub Actions via SSH, ou na mão: bash ~/notam_schedule/deploy/atualizar.sh):
# atualiza o código para a branch do deploy e reconstrói o container da API de NOTAMs.
# O .env (credenciais da AISWEB e NOTAM_API_KEY) não está no Git e não é mexido.
# data/ (cache, histórico bruto da AISWEB, execuções) fica fora do Git e continua lá.
# Tudo dentro de { }: o git reset abaixo pode trocar este próprio arquivo no meio da execução.
{
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
# a chave de deploy tem command= fixo no authorized_keys, então DEPLOY_BRANCH do workflow
# não chega aqui; sem ele, usa o branch padrão do GitHub (origin/HEAD)
git remote set-head origin --auto >/dev/null 2>&1 || true
DEFAULT_BRANCH="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')"
BRANCH="${DEPLOY_BRANCH:-${DEFAULT_BRANCH:-master}}"

echo "==> $APP_DIR @ $BRANCH"
[ -f .env ] || { echo "ERRO: falta $APP_DIR/.env (copie .env.example: AISWEB_API_KEY, AISWEB_API_PASS, NOTAM_API_KEY)"; exit 1; }
grep -qE '^NOTAM_API_KEY=.+' .env || { echo "ERRO: defina NOTAM_API_KEY no .env (a API fica na rede do Caddy)"; exit 1; }

git fetch --prune origin "$BRANCH"
git checkout -q "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "==> commit $(git log -1 --format='%h %s')"

NET="$(grep -E '^PROXY_NETWORK=' .env | cut -d= -f2- || true)"
NET="${NET:-fortran_default}"
docker network inspect "$NET" >/dev/null 2>&1 \
  || { echo "ERRO: rede docker '$NET' não existe (o Caddy do ~/fortran está no ar? ajuste PROXY_NETWORK no .env)"; exit 1; }

docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null

echo "==> esperando /api/health"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8002/api/health >/dev/null; then
    echo "==> ok: $(curl -fsS http://127.0.0.1:8002/api/health)"; exit 0
  fi
  sleep 2
done
echo "ERRO: container não respondeu; últimos logs:"
docker compose logs --tail 50 notam-schedule
exit 1
}
