# Prumo no Linux por SSH

Exemplo para Ubuntu/Debian com Python 3.11 ou superior, systemd e domínio apontado para o servidor. O Git contém somente código e um conjunto inicial vazio. Para levar sua carteira atual, exporte um JSON pelo painel local e importe no painel do servidor. Nunca envie esse backup ao GitHub.

## 1. Clonar e preparar

Execute como seu usuário normal com acesso a sudo. Se o repositório for privado, autentique o Git com sua conta ou uma deploy key de leitura.

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
python3 --version
sudo install -d -o "$USER" -g "$(id -gn)" -m 755 /opt/prumo
git clone https://github.com/mpereira356/prumo.git /opt/prumo
cd /opt/prumo
bash deploy/setup.sh
```

Crie um usuário de serviço sem login e diretórios separados para configuração e dados. Se o usuário já existir, pule o comando `useradd`.

```bash
sudo useradd --system --user-group --home-dir /var/lib/prumo --shell /usr/sbin/nologin prumo
sudo install -d -o prumo -g prumo -m 700 /var/lib/prumo
sudo install -d -o root -g prumo -m 750 /etc/prumo
sudo install -o root -g prumo -m 640 /dev/null /etc/prumo/prumo.env
```

O código em `/opt/prumo` precisa ser legível pelo usuário de serviço, inclusive a `.venv`. O serviço não deve ser proprietário do código. Se sua umask for restritiva, ajuste a leitura do checkout antes de iniciar:

```bash
chmod -R a+rX /opt/prumo
```

Ainda não coloque segredos no checkout. Gere uma chave diretamente no arquivo protegido, sem exibi-la no terminal:

```bash
sudo /opt/prumo/.venv/bin/python - <<'PY'
import secrets
from pathlib import Path
path = Path('/etc/prumo/prumo.env')
if path.stat().st_size:
    raise SystemExit('Arquivo já preenchido; preserve a chave existente.')
path.write_text(
    'APP_ENV=production\n'
    'SECRET_KEY=' + secrets.token_hex(32) + '\n'
    'TRUSTED_HOSTS=prumo.seudominio.com\n'
    'PRUMO_INSTANCE_PATH=/var/lib/prumo\n'
    'BRAPI_TOKEN=\n'
)
PY
sudoedit /etc/prumo/prumo.env
```

Troque `prumo.seudominio.com` pelo domínio real. Não coloque protocolo ou caminho em `TRUSTED_HOSTS`. A chave brapi é opcional; use uma nova chave, pois a do HTML antigo estava exposta. Não gere outra `SECRET_KEY` a cada atualização.

## 2. Criar acesso e iniciar

```bash
sudo -u prumo bash -c 'set -a; source /etc/prumo/prumo.env; set +a; cd /opt/prumo; exec .venv/bin/python -m flask --app app create-admin'
sudo install -o root -g root -m 644 deploy/prumo.service /etc/systemd/system/prumo.service
sudo systemctl daemon-reload
sudo systemctl enable --now prumo
sudo systemctl status prumo --no-pager
```

O comando de criação solicita usuário e senha. Todos os usuários autorizados compartilham esta mesma carteira pessoal. Recuperação de senha usa o mesmo comando de ambiente acima, substituindo `create-admin` por `reset-password`.

## 3. HTTPS

Instale o Caddy seguindo as [instruções oficiais para sua distribuição](https://caddyserver.com/docs/install). Adicione o bloco de `deploy/Caddyfile.example` à configuração existente em `/etc/caddy/Caddyfile`, trocando o domínio. Preserve os outros sites do servidor.

```bash
sudoedit /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

O domínio deve apontar ao IP do servidor e as portas 80/443 precisam estar disponíveis. Caddy gerencia o certificado e redireciona HTTP para HTTPS ([documentação](https://caddyserver.com/docs/automatic-https)). Acesse `https://prumo.seudominio.com`. O Waitress escuta somente em loopback; não abra a porta 5000 para a internet. Não use acesso HTTP direto em produção: os cookies Secure exigem HTTPS.

O serviço mantém o sistema de arquivos somente para leitura, exceto `/var/lib/prumo`, usando [restrições do systemd](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html). Os logs ficam em `journalctl -u prumo`. Não há redirecionamento de logs de requisições com dados financeiros para o Git.

## 4. Atualizar com git pull

Como o banco está em `/var/lib/prumo`, atualizações do código não substituem os dados. Antes de atualizar, faça um backup consistente com o serviço parado:

```bash
cd /opt/prumo
git status --short
git pull --ff-only
sudo systemctl stop prumo
sudo install -d -o root -g root -m 700 /var/backups/prumo
sudo /opt/prumo/.venv/bin/python - <<'PY'
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
os.umask(0o077)
source = Path('/var/lib/prumo/financeiro.sqlite3')
if not source.is_file():
    raise SystemExit('Banco não encontrado; verifique PRUMO_INSTANCE_PATH.')
target = Path('/var/backups/prumo') / ('prumo-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
    src.backup(dst)
print('Backup concluído.')
PY
.venv/bin/python -m pip --isolated install -r requirements.lock.txt
PRUMO_INSTANCE_PATH=/tmp/prumo-tests .venv/bin/python -m unittest discover -s tests -v
sudo systemctl start prumo
sudo systemctl status prumo --no-pager
```

Execute cada comando conferindo seu resultado. Se a instalação ou os testes falharem, não avance para iniciar a nova versão: corrija a falha ou retorne ao commit anterior conhecido. Não remova o banco para corrigir falhas de inicialização. Se `prumo.service` mudar, copie-o novamente e execute `systemctl daemon-reload` antes de iniciar. Em mudanças futuras de esquema, siga as instruções de migração da versão antes do reinício.

Guarde também uma cópia protegida dos backups fora do servidor. Backups JSON preservam os dados financeiros; o backup SQLite inclui os usuários e deve receber a mesma proteção de uma credencial.

## Diagnóstico

```bash
sudo journalctl -u prumo -n 80 --no-pager
sudo systemctl restart prumo
```

Erro de Host: confira `TRUSTED_HOSTS` e o domínio do proxy. Login que não persiste: confira HTTPS, configuração de produção e relógio do servidor. Não altere os cookies Secure para contornar uma implantação sem TLS.
