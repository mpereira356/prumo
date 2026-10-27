# Prumo

**Seu patrimônio na direção certa.** Painel pessoal em Flask e SQLite para organizar investimentos, recebíveis, fluxo de caixa, metas e aportes.

## Linux / servidor SSH

**Siga [o guia de implantação Linux](deploy/README.md)**: instalação, serviço systemd com Waitress, HTTPS com Caddy, backup e atualização por `git pull`.

O código fica em `/opt/prumo`, o banco em `/var/lib/prumo` e os segredos em `/etc/prumo/prumo.env`. Atualizações não substituem dados ou credenciais.

Pré-requisitos: Python 3.11+, Git e, para produção, systemd e HTTPS.

```bash
git clone https://github.com/mpereira356/prumo.git
cd prumo
bash deploy/setup.sh
# Desenvolvimento local; para produção, siga o guia acima.
.venv/bin/python -m flask --app app create-admin
.venv/bin/python app.py
```

Abra http://127.0.0.1:5000 em desenvolvimento. Não exponha esse servidor de desenvolvimento na internet.

## Windows

Execute `./iniciar.ps1` no PowerShell. O assistente prepara o ambiente, solicita a criação do primeiro usuário e inicia a aplicação. Alternativamente:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock.txt
.\.venv\Scripts\python -m flask --app app create-admin
.\.venv\Scripts\python app.py
```

Não há senha padrão nem cadastro público. Use uma senha de pelo menos 12 caracteres. Para recuperar o acesso, execute `python -m flask --app app reset-password` no mesmo ambiente; o comando revoga as sessões existentes.

## Funcionalidades

- Carteira com busca, filtros, cadastro, edição e exclusão de posições.
- Cotações B3 opcionais pelo servidor com token de ambiente.
- Contratos parcelados, cheques, juros mensais e amortizações.
- Fluxo de 36 meses, atrasos, confirmação idempotente e reversão de pagamentos.
- Metas, simulação de juros compostos e histórico de aportes.
- Exportação CSV, backup JSON e importação validada e atômica de backups v2/v3.
- Interface responsiva, navegação por teclado e tema claro/escuro.

## Dados e migração

Um clone novo começa vazio. O repositório não inclui registros financeiros pessoais, banco, senhas, chave de sessão, imagens com saldos nem HTML legado com token exposto. O conjunto vazio está em `data/initial.example.json`. Uma cópia local opcional em `data/initial.json`, ignorada pelo Git, é utilizada somente na primeira inicialização.

Para migrar do painel antigo, exporte um JSON no HTML usando o mesmo navegador/origem onde os dados estavam salvos e importe em **Backup e migração**, no rodapé. A importação substitui os dados financeiros depois da validação completa. Exporte uma cópia do estado atual antes. Grupos expandidos e preferências de reinvestimento do HTML não são migrados.

Em desenvolvimento, o banco fica em `instance/financeiro.sqlite3`. Em produção, `PRUMO_INSTANCE_PATH` define a pasta persistente. O JSON inclui dados financeiros, mas não senhas. O backup SQLite inclui usuários e exige proteção adicional. O banco não é criptografado pela aplicação: use criptografia do disco e permissões do sistema operacional.

Todos os usuários criados pelo CLI acessam a mesma carteira pessoal. Não há isolamento multicliente.

## Cálculos e cotações

Principal a amortizar fica separado das parcelas de juros. Cheques e parcelas previstas não são saldo disponível. A amortização reduz o principal e recalcula os juros pendentes, preservando valores já confirmados. Aportes mantêm um histórico; atualize as posições após compras, pois o registro do aporte não aumenta quantidades automaticamente.

Simulações usam juros compostos sobre a carteira atual, sem novos aportes, impostos, taxas ou resgates. Não representam retorno garantido.

O botão **Cotações B3** requer `BRAPI_TOKEN` no ambiente e plano compatível com consultas em lote. Aceita uma tentativa a cada cinco minutos e preserva preços sem resposta. Foi testado com respostas simuladas; consultas reais exigem sua credencial. Use uma chave nova: a chave do HTML antigo precisa ser revogada. [Documentação oficial da brapi](https://brapi.dev/docs/examples/python).

## Configuração e segurança

`.env.example` documenta as variáveis; não é carregado automaticamente. Em produção, configure `APP_ENV=production`, uma `SECRET_KEY` aleatória persistente de pelo menos 32 caracteres, `TRUSTED_HOSTS` e `PRUMO_INSTANCE_PATH`. Não gere outra chave a cada atualização.

Inclui senhas com scrypt, SQL parametrizado, escape de templates, CSRF nos POSTs, limite persistente de tentativas, sessões revogáveis de 30 minutos, CSP sem scripts inline/CDNs, bloqueio de iframes, limites de requisição e validação de importações. Em produção, ativa cookies Secure e HSTS. [Referência de segurança do Flask](https://flask.palletsprojects.com/en/stable/web-security/).

HTTPS, backups, atualização de dependências e proteção do servidor continuam necessários. A aplicação não confia em `X-Forwarded-For`; atrás de um proxy, o limite por IP é compartilhado de forma conservadora. Segurança absoluta não existe.

## Verificação

```bash
.venv/bin/python -m unittest discover -s tests -v
```

No Windows, use `.\.venv\Scripts\python`. Os testes usam bancos temporários e dados sintéticos, verificando acesso privado, CSRF, cabeçalhos, renderização, XSS, números inválidos, backups atômicos, revogação de sessões, limite de login, pagamentos, amortizações e integração simulada de cotações.

`node --check static/app.js` verifica a sintaxe JavaScript. `tests/browser_smoke.py` é uma verificação opcional que requer Playwright e Chromium instalados separadamente; não são dependências do servidor.
