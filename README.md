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

- Meus investimentos com busca, filtros, compras limitadas pelo saldo e resgates.
- Carteira de dinheiro disponível, adição de saldo com origem e Extrato de entradas e saídas.
- Cotações B3 opcionais pelo servidor com token de ambiente.
- Dólar comercial USD/BRL no cabeçalho de todas as páginas, com variação e horário de Brasília; atualização automática a cada cinco minutos.
- Contratos parcelados, cheques, juros mensais e amortizações.
- Recebíveis com edição, exclusão confirmada, filtros de situação, próxima parcela e cronograma por contrato.
- Registro de pagamento com separação entre juros e amortização, simulação do novo principal e histórico com reversão do último lançamento.
- Fluxo de 36 meses, atrasos, confirmação idempotente e reversão de pagamentos.
- Metas, simulação de juros compostos e histórico de aportes.
- Exportação CSV, backup JSON e importação validada e atômica de backups v2/v3/v4.
- Interface responsiva, navegação por teclado e tema claro/escuro.

## Dados e migração

Um clone novo começa vazio. O repositório não inclui registros financeiros pessoais, banco, senhas, chave de sessão, imagens com saldos nem HTML legado com token exposto. O conjunto vazio está em `data/initial.example.json`. Uma cópia local opcional em `data/initial.json`, ignorada pelo Git, é utilizada somente na primeira inicialização.

Para migrar do painel antigo, exporte um JSON no HTML usando o mesmo navegador/origem onde os dados estavam salvos e importe em **Backup e migração**, no rodapé. A importação substitui os dados financeiros depois da validação completa. Exporte uma cópia do estado atual antes. Grupos expandidos e preferências de reinvestimento do HTML não são migrados.

Em desenvolvimento, o banco fica em `instance/financeiro.sqlite3`. Em produção, `PRUMO_INSTANCE_PATH` define a pasta persistente. O JSON inclui dados financeiros, mas não senhas. O backup SQLite inclui usuários e exige proteção adicional. O banco não é criptografado pela aplicação: use criptografia do disco e permissões do sistema operacional.

Todos os usuários criados pelo CLI acessam a mesma carteira pessoal. Não há isolamento multicliente.

## Carteira, investimentos e extrato

**Carteira** é dinheiro disponível, separado do patrimônio aplicado em **Meus investimentos**. O card aparece na Visão geral e nos investimentos. O botão **Adicionar saldo** pede o valor e a origem e gera uma entrada no **Extrato**. Como combinado para a migração, a Carteira começa em **R$ 0**: pagamentos confirmados antes da ativação não são creditados novamente. Informe manualmente o saldo inicial realmente disponível. Essa migração é executada uma única vez, sem apagar posições ou recebíveis.

Novos recebimentos confirmados entram automaticamente, incluindo cheques, juros, multa, mora e principal amortizado. Confirmações repetidas não duplicam o crédito. Estornos, mudanças de cheque pago para não pago e exclusões de contratos revertem o valor correspondente. Se esse dinheiro já tiver sido investido e faltar saldo para o estorno, a operação é bloqueada: resgate investimentos ou adicione saldo antes de desfazer o recebimento.

**Nova posição** desconta da Carteira o custo de aquisição, calculado por quantidade × preço médio de compra. Compras acima do saldo são rejeitadas no servidor, inclusive em requisições simultâneas. A cotação de mercado não movimenta dinheiro. A edição preserva quantidade e custo; compras adicionais podem ser cadastradas como novas posições. **Resgatar** registra a quantidade vendida e o preço efetivamente recebido por unidade, reduz a posição e devolve o dinheiro à Carteira. Excluir o cadastro de uma posição não simula venda e não credita dinheiro automaticamente. As operações são registros pessoais; não enviam ordens a corretoras.

O **Extrato** mostra valores positivos e negativos, origem/destino, horário de Brasília e saldo após cada movimento. A barra de **Desfazer** também restaura saldo e extrato junto com a operação. Aportes antigos da página de metas continuam como histórico e não movimentam a Carteira; para movimentar dinheiro, use Adicionar saldo ou Nova posição.

Backups **v4** incluem saldo, extrato e conciliação dos recebimentos. Importações antigas (v2/v3), que não possuem Carteira, substituem os dados e reiniciam seu saldo disponível em R$ 0; confirme o saldo inicial manualmente após importar. Faça backup antes de substituir dados. Os valores do caixa são armazenados em centavos inteiros para evitar erros de ponto flutuante.

## Cálculos e cotações

Principal a amortizar fica separado das parcelas de juros. Cheques e parcelas previstas não são saldo disponível. A amortização reduz o principal e recalcula os juros pendentes, preservando valores já confirmados. Aportes mantêm um histórico; atualize as posições após compras, pois o registro do aporte não aumenta quantidades automaticamente.

Simulações usam juros compostos sobre a carteira atual, sem novos aportes, impostos, taxas ou resgates. Não representam retorno garantido.

No cartão de recebíveis, clique no número de parcelas/cheques para ver datas, valores e situação. O cronograma também mostra o início previsto da amortização, quando informado. **Registrar pagamento** nos contratos de juros e amortização cobre os juros pendentes da parcela e usa o excedente para reduzir o principal; pagamentos inferiores aos juros pendentes são rejeitados. **Histórico** permite consultar a divisão e desfazer o último lançamento. A opção **Registrar amortização** recebe somente principal, sem somar juros. Confirmações simples de parcelas continuam no fluxo de caixa.

No cronograma de cheques pré-datados, cada cheque tem ações **Pagamento confirmado**, **Em atraso** e **No prazo**. A confirmação mantém a linha, marca como **Pago** e atualiza os totais, inclusive para pagamento antecipado. É possível desfazer o pagamento; marcar um cheque pago em atraso remove a confirmação após aviso. Marcações manuais de prazo prevalecem sobre o calendário; na ausência delas, o atraso é automático após o vencimento. Os estados são preservados nos backups JSON. Após cada ação, o cronograma do mesmo contrato reabre automaticamente.

Todos os outros cronogramas também possuem **Pagamento confirmado** e **Desfazer pagamento** em cada parcela. Confirmações integrais podem ser antecipadas e mantêm a parcela no histórico como **Pago**. Em contratos de juros, esse botão confirma somente os juros previstos; para receber principal junto, utilize **Registrar pagamento**. Recebimentos vinculados a amortizações devem ser desfeitos pelo histórico do contrato.

### Multa e juros de mora

Ao criar ou editar um contrato, informe **Juros de mora por mês (%)** e **Multa por atraso (%)**. Os valores são os percentuais contratados pelo usuário; não há taxa preenchida automaticamente. Informe `0` para não cobrar um encargo. Contratos antigos sem um dos campos exibem um aviso no cartão, com atalho para completar a configuração. As condições originais permanecem protegidas após pagamentos, mas mora e multa podem ser configuradas para as parcelas ainda pendentes.

Após o vencimento, calcula-se sobre o saldo base pendente da parcela:

- Multa única = saldo pendente × percentual de multa ÷ 100.
- Mora simples = saldo pendente × percentual mensal de mora × dias corridos de atraso ÷ 3000.
- Total atualizado = valor base + multa + mora; sem capitalização e sem juros sobre os próprios encargos.

No dia do vencimento não há encargos. Exemplo matemático: parcela de R$ 1.000, multa de 2% e mora mensal de 1%, com 15 dias de atraso, totaliza R$ 1.025. Esse exemplo apenas explica a fórmula e não recomenda percentuais.

Os valores são recalculados na consulta ao painel, usando a data de Brasília, e aparecem discriminados no cronograma, no fluxo e nos totais. Configurar taxas em contrato antigo atualiza parcelas pendentes já vencidas desde seu vencimento, sem alterar recebimentos anteriormente confirmados. A marcação manual de cheque como **No prazo** altera somente a situação informada; não muda o vencimento nem suspende encargos.

Ao confirmar um pagamento, seus encargos são fixados e deixam de aumentar. Desfazer a confirmação reabre a parcela e recalcula o atraso até a data atual. O registro combinado de juros/amortização calcula multa e mora até a **data do pagamento informada**, cobra os encargos primeiro e usa somente o excedente para amortizar o principal. Backup e importação preservam taxas, valores pagos e detalhamento dos encargos.

Contratos com pagamentos preservam suas condições financeiras durante a edição: nome, apelido, risco e observações podem ser corrigidos; mora e multa podem ser configuradas para parcelas pendentes. Para corrigir condições de um contrato pago, revise e desfaça os pagamentos aplicáveis antes. A exclusão remove o contrato e seus recebimentos após confirmação; exporte backup antes. A importação do HTML mais recente preserva os campos de juros, amortização, saldo anterior e posterior no histórico. Recebimentos parciais legados são indicados como parciais.

O botão **Cotações B3** requer `BRAPI_TOKEN` no ambiente e plano compatível com consultas em lote. Aceita uma tentativa a cada cinco minutos e preserva preços sem resposta. Foi testado com respostas simuladas; consultas reais exigem sua credencial. Use uma chave nova: a chave do HTML antigo precisa ser revogada. [Documentação oficial da brapi](https://brapi.dev/docs/examples/python).

O dólar consulta primeiro o preço de compra comercial da [AwesomeAPI](https://docs.awesomeapi.com.br/api-de-moedas), sem depender da chave brapi. A variável opcional `AWESOMEAPI_TOKEN` autentica essa consulta pelo cabeçalho `x-api-key` e permite utilizar os limites da sua conta. Se a fonte principal falhar ou atingir o limite de requisições, consulta automaticamente a [Frankfurter](https://frankfurter.dev/) para obter a referência diária do Banco Central Europeu, identificada na tela como **Referência diária**, sem inventar variação percentual intradiária.

As consultas passam pelo servidor, usam destinos fixos, timeout e cache persistente compartilhado de cinco minutos. O horário da AwesomeAPI é o da negociação convertido para Brasília; referências diárias mostram apenas a data. Se ambas as fontes falharem, mantém o último valor disponível e tenta novamente após um minuto, em vez de esperar cinco minutos. Sem valor anterior, informa indisponibilidade. Os provedores podem servir dados em cache; não representa cotação de turismo nem preço garantido para operação.

## Desfazer alterações

Após salvar, excluir ou confirmar alterações financeiras importantes, aparece no canto inferior direito uma barra de cinco segundos com **Desfazer**. Inclui posições, contratos, pagamentos, amortizações, situações de cheques, metas, aportes, atualização das cotações B3 e importações. Alterações sem mudança de dados não criam outra reversão. Login, senhas, tema e consultas do dólar não participam dessa função.

A janela começa na primeira exibição após a ação; navegar ou recarregar não reinicia a contagem. O prazo também é validado no servidor. A reversão exige a mesma conta e sessão, proteção CSRF e que nenhum dado financeiro tenha mudado desde a ação. Se outra alteração ocorrer, o servidor recusa a reversão antiga para preservar os dados mais recentes. A barra funciona também com o cronograma aberto no celular.

O estado anterior é guardado temporariamente no SQLite, sem enviar dados financeiros ao cookie ou ao Git. Registros expirados são limpos nas próximas ações/exibições da barra. Essa janela curta complementa as confirmações e os backups, sem substituí-los.

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
