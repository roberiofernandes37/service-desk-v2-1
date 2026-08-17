# Service Desk V2.1 — Operação, Arquitetura e Manutenção

**Status:** documentação da versão atual do repositório
**Público:** desenvolvimento, suporte técnico, administração e futuras equipes de manutenção
**Porta local padrão:** 18437 no host, encaminhada para 8000 no container web

Este documento é a referência técnica e operacional do Service Desk V2.1. Ele descreve o comportamento implementado no código atual, os procedimentos de instalação e operação, os cuidados para alterações futuras e os caminhos de diagnóstico.

> Regra de segurança: nunca publique .env, instance/mail_config.json, dumps de banco, backups, anexos ou logs com dados reais no GitHub.

## 1. Visão geral

O Service Desk V2.1 é uma aplicação web Flask para registro, acompanhamento e atendimento de solicitações internas. O sistema possui:

- autenticação de usuários e perfis de acesso;
- dashboard com indicadores de fila, SLA e carga de trabalho;
- lista operacional com filtros, paginação e exportação CSV;
- visão Kanban por status;
- criação e edição de solicitações abertas;
- múltiplos anexos por solicitação, com download protegido e exclusão lógica;
- campos dinâmicos definidos por categoria;
- fluxo com assumir, pausar, retomar, concluir, cancelar e reabrir;
- histórico, comentários e notificações internas;
- notificações de e-mail configuráveis por usuário;
- configuração SMTP fora do banco de dados;
- auditoria de ações administrativas e operacionais;
- registro de erros no banco e em arquivo de fallback;
- backup e restauração do banco, anexos e logs;
- execução local em Docker com PostgreSQL.

## 2. Arquitetura técnica

### 2.1 Componentes

~~~text
Navegador
   |
   | HTTP :18437
   v
Gunicorn :8000 (container web)
   |
   +-- Flask application factory (app.create_app)
   |      +-- Blueprints: auth, main, tickets, admin
   |      +-- Flask-Login / CSRF / SQLAlchemy / Migrate
   |      +-- Serviços: SLA, workflow, uploads, e-mail, backup, auditoria, erros
   |
   +-- PostgreSQL :5432 (container db, sem porta publicada no host)
   |
   +-- Volumes: uploads, logs, backups, instance e dados PostgreSQL
~~~

### 2.2 Estrutura do projeto

~~~text
service-desk-v2-1/
├── app/
│   ├── __init__.py              # factory, CLI e handlers globais
│   ├── config.py                # configurações por ambiente
│   ├── extensions.py            # extensões Flask
│   ├── forms.py                 # formulários e validações
│   ├── models.py                # modelos SQLAlchemy
│   ├── security.py              # visibilidade e permissões
│   ├── routes/
│   │   ├── auth.py              # login, senha e preferências
│   │   ├── main.py              # dashboard e notificações
│   │   ├── tickets.py           # demandas, Kanban, workflow e anexos
│   │   └── admin.py             # administração e relatórios
│   ├── services/
│   │   ├── audit.py             # auditoria
│   │   ├── backup.py            # backup e restauração
│   │   ├── error_logging.py     # erros e mascaramento
│   │   ├── mail_service.py      # SMTP e JSON
│   │   ├── notifications.py     # avisos internos e e-mail
│   │   ├── sla.py               # estado e duração do SLA
│   │   ├── ticket_workflow.py   # transições de status
│   │   └── uploads.py           # anexos e proteção de caminhos
│   ├── static/css/app.css       # visual responsivo
│   └── templates/               # Jinja2, Bootstrap e Font Awesome
├── scripts/diagnose_email.py
├── tests/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── wsgi.py
└── .env.example
~~~

### 2.3 Application factory

app.create_app() é o ponto de inicialização. A ordem principal é:

1. carrega o .env;
2. seleciona Config, ProductionConfig ou TestingConfig;
3. cria os diretórios de instance, uploads, logs e backups;
4. inicializa SQLAlchemy, Flask-Migrate, CSRF, LoginManager e Flask-Mail;
5. registra os blueprints;
6. registra comandos CLI, contexto de templates e handlers de erro.

Para adicionar uma funcionalidade web, prefira criar ou ampliar um blueprint e manter regras complexas em app/services/, evitando lógica de negócio no template.

## 3. Ambientes, Docker e portas

### 3.1 Serviços Docker

| Serviço | Função | Porta publicada |
|---|---|---|
| web | Flask servido por Gunicorn | 18437:8000 |
| db | PostgreSQL 15 | nenhuma |

O banco só é acessível pela rede interna do Compose. Não publique a porta do PostgreSQL sem uma necessidade operacional clara.

### 3.2 Portas reservadas

Use http://localhost:18437. Não alterar para 8080, 8081, 5173 ou 8091.

### 3.3 Volumes

| Volume | Caminho no container | Conteúdo | Impacto se perdido |
|---|---|---|---|
| postgres_data | /var/lib/postgresql/data | banco PostgreSQL | perda dos dados estruturados |
| uploads_data | /srv/service-desk/uploads | anexos | perda dos arquivos das demandas |
| logs_data | /srv/service-desk/logs | logs e erros de fallback | perda do histórico técnico em arquivo |
| backups_data | /srv/service-desk/backups | pacotes de backup | perda dos backups locais |
| instance_data | /srv/service-desk/instance | mail_config.json | perda da configuração SMTP |

Nunca executar docker-compose down -v em ambiente com dados que precisem ser preservados.

## 4. Instalação e primeiro acesso

### 4.1 Pré-requisitos

- Windows 10/11 com Docker Desktop ativo;
- Git e PowerShell;
- acesso ao repositório;
- espaço para imagens, banco, anexos, logs e backups.

### 4.2 Inicialização

Na pasta raiz:

~~~powershell
Copy-Item .env.example .env
notepad .env
~~~

Altere pelo menos:

~~~dotenv
SECRET_KEY=uma-chave-longa-e-aleatoria
POSTGRES_PASSWORD=uma-senha-forte-do-postgres
~~~

Suba os serviços:

~~~powershell
docker-compose up -d --build
~~~

Inicialize o banco e os registros-base:

~~~powershell
docker-compose exec web flask init-db
docker-compose exec web flask seed-admin --email admin@empresa.com.br --password "troque-esta-senha"
~~~

Acesse:

~~~text
http://localhost:18437
~~~

Troque a senha inicial imediatamente após o primeiro acesso.

### 4.3 Saúde e logs

~~~powershell
Invoke-WebRequest http://localhost:18437/health
docker-compose ps
docker-compose logs -f web
docker-compose logs -f db
~~~

Resposta esperada do health:

~~~json
{"status":"ok"}
~~~

### 4.4 Atualização sem apagar dados

~~~powershell
git pull --ff-only origin main
docker-compose up -d --build
docker-compose exec web flask init-db
~~~

Faça backup antes de alterações de banco ou atualização com mudança de modelo.

## 5. Configuração por ambiente

As variáveis são carregadas por app/config.py. O Compose injeta algumas configurações diretamente no container web.

| Variável | Finalidade | Padrão/desenvolvimento | Produção |
|---|---|---|---|
| FLASK_ENV | ambiente | development | production |
| SECRET_KEY | sessões e CSRF | chave de desenvolvimento | obrigatória e forte |
| POSTGRES_PASSWORD | senha do PostgreSQL | .env | obrigatória |
| DATABASE_URL | conexão SQLAlchemy | SQLite em instance | PostgreSQL interno |
| UPLOAD_ROOT | anexos | uploads | /srv/service-desk/uploads |
| LOG_ROOT | logs | logs | /srv/service-desk/logs |
| BACKUP_ROOT | backups | backups | /srv/service-desk/backups |
| MAX_UPLOAD_MB | limite de upload | 50 | 50 no Compose |
| COOKIE_SECURE | cookies somente HTTPS | false | true atrás de HTTPS |

Em produção com HTTPS, use COOKIE_SECURE=true somente quando o proxy TLS estiver corretamente configurado.

## 6. Banco de dados

O banco oficial do Docker é PostgreSQL. Os testes usam SQLite em memória. Os modelos estão em app/models.py.

| Tabela/modelo | Responsabilidade |
|---|---|
| access_profiles | perfis e permissões |
| users | usuários, hash de senha e perfil |
| user_notification_preferences | eventos de e-mail por usuário |
| branches | filiais ou escopos gerais |
| categories | categorias |
| dynamic_fields | campos configuráveis por categoria |
| tickets | demanda, status, SLA e responsáveis |
| ticket_attachments | anexos, autoria e exclusão lógica |
| ticket_history | linha do tempo |
| ticket_comments | comentários |
| notifications | avisos internos |
| audit_logs | auditoria |
| system_error_logs | erros tratados |
| backup_configs | retenção e horários |
| backup_runs | histórico de backup/restauração |

### 6.1 init-db

flask init-db:

- cria tabelas ausentes com db.create_all();
- adiciona completed_at e resolved_by_id quando necessário;
- migra initial_file e final_file para ticket_attachments;
- cria Administrador e Atendente se não existirem;
- cria GERAL se nenhuma categoria existir.

Importante: não há uma sequência completa de migrations versionadas para qualquer alteração arbitrária de coluna. Antes de mudar tabelas em produção, faça backup e implemente uma migração explícita ou um passo idempotente no init-db.

## 7. Usuários, perfis e segurança

### 7.1 Permissões

| Permissão | Uso |
|---|---|
| can_manage_users | usuários e perfis |
| can_manage_settings | parâmetros, categorias, filiais e SMTP |
| can_reset_data | backup e restauração |
| can_work_tickets | assumir, pausar, retomar, concluir e transferir |
| can_view_reports | relatórios, auditoria e erros |

Administrador recebe todas as permissões. Atendente recebe can_work_tickets.

### 7.2 Visibilidade

- can_manage_settings: todas as demandas;
- can_view_reports: todas as demandas;
- can_work_tickets: fila operacional;
- demais usuários: demandas em que são solicitantes ou responsáveis.

O backend sempre revalida a permissão. Esconder um botão no HTML não é controle de segurança.

### 7.3 Controles de segurança

- senha com hash do Werkzeug;
- CSRF nos formulários;
- login somente para usuário ativo;
- logout por POST;
- downloads protegidos;
- rejeição de path traversal em anexos e backups;
- mascaramento de password, senha, token, secret, csrf e key nos logs;
- SMTP fora do banco e do Git.

## 8. Fluxo de uma demanda

### 8.1 Dados obrigatórios

Título, descrição, prioridade, categoria e prazo SLA são obrigatórios. A categoria começa em “Selecione uma categoria” e não pode permanecer nessa opção.

Campos dinâmicos também podem ser obrigatórios; a validação é feita no backend por collect_custom_data.

### 8.2 Estados e transições

| Estado atual | Ação | Próximo estado | Regra |
|---|---|---|---|
| Aberta | Assumir | Em Andamento | vincula o responsável |
| Aberta | Cancelar | Cancelada | justificativa obrigatória |
| Em Andamento | Pausar | Pausada | justificativa obrigatória e SLA congelado |
| Em Andamento | Concluir | Concluida | solução obrigatória |
| Em Andamento | Cancelar | Cancelada | justificativa obrigatória |
| Pausada | Retomar | Em Andamento | soma o tempo pausado ao prazo |
| Pausada | Cancelar | Cancelada | justificativa obrigatória |
| Concluida | Reabrir | Em Andamento | remove o encerramento |
| Cancelada | — | — | estado terminal atual |

As transições ficam centralizadas em app/services/ticket_workflow.py.

### 8.3 SLA

- created_at inicia a contagem;
- pause_started_at marca o início da pausa;
- total_paused_seconds acumula pausas;
- retomar estende due_at pelo tempo pausado;
- active_seconds() desconta pausas;
- concluídas e canceladas deixam de ser SLA ativo;
- concluídas mantêm completed_at, resolved_by_id e resolution_note.

## 9. Anexos

- vários arquivos podem ser enviados na criação;
- vários arquivos podem ser adicionados na edição de demanda aberta;
- conclusão aceita arquivos finais;
- limite atual: MAX_UPLOAD_MB=50;
- extensões: txt, pdf, png, jpg, jpeg, csv, xlsx, xls, doc, docx, xml, ppt e pptx;
- nomes são sanitizados com secure_filename;
- arquivos recebem ID da demanda, tipo e UUID;
- armazenamento é organizado por ano e mês;
- download passa por rota protegida;
- exclusão é lógica, usando deleted_at;
- arquivos legados são migrados pelo init-db.

Não apague manualmente uploads sem verificar ticket_attachments, initial_file e final_file. Gere backup antes de limpeza.

## 10. E-mail e notificações

### 10.1 SMTP

O arquivo fica em:

~~~text
instance/mail_config.json
~~~

No Docker, ele é persistido pelo volume instance_data. Estrutura sem senha real:

~~~json
{
  "server": "smtp.gmail.com",
  "port": 587,
  "username": "notificacoes@empresa.com",
  "password": "SENHA_DE_APLICATIVO",
  "sender": "notificacoes@empresa.com",
  "test_recipient": "admin@empresa.com",
  "use_tls": true,
  "use_ssl": false
}
~~~

TLS e SSL não podem ser usados simultaneamente. Para Gmail, prefira senha de aplicativo.

### 10.2 Teste e salvamento

- Apenas salvar grava o JSON e reaplica a configuração;
- Testar conexão usa os dados temporariamente e só grava se o envio funcionar;
- falha no teste preserva a configuração anterior;
- falha SMTP não deve quebrar a ação de negócio.

### 10.3 Eventos por usuário

Eventos disponíveis:

- demanda pausada;
- demanda concluída;
- demanda reaberta;
- demanda cancelada;
- novo comentário.

Criação e atribuição não enviam e-mail para evitar excesso. O envio ocorre somente com email_enabled ativo e evento selecionado. Notificações internas seguem seu próprio fluxo.

### 10.4 Diagnóstico

~~~powershell
docker-compose exec web python scripts/diagnose_email.py
docker-compose exec web python scripts/diagnose_email.py --send-test --recipient seu-email@empresa.com.br
~~~

O diagnóstico verifica configuração, DNS, TCP, STARTTLS, autenticação e envio opcional. A senha não deve aparecer no terminal.

## 11. Backup e restauração

### 11.1 Conteúdo

O pacote tar.gz contém database.sql, metadata.json e, quando habilitado, uploads/ e logs/.

PostgreSQL usa pg_dump. SQLite usa iterdump.

### 11.2 Configuração

A tela Backup permite configurar:

- backup ativo;
- horários HH:MM,HH:MM;
- quantidade máxima;
- inclusão de anexos;
- inclusão de logs.

O agendamento depende do Agendador de Tarefas do Windows ou equivalente.

### 11.3 Comandos

~~~powershell
docker-compose exec web flask backup-create
docker-compose exec web flask backup-create --no-uploads --no-logs
docker-compose exec web flask backup-list
docker-compose exec web flask backup-test --id ID
~~~

Restauração por histórico:

~~~powershell
docker-compose exec web flask backup-restore --id ID --confirm RESTAURAR
~~~

Restauração por arquivo:

~~~powershell
docker-compose exec web flask backup-restore --file /srv/service-desk/backups/arquivo.tar.gz --confirm RESTAURAR
~~~

A restauração cria um backup de segurança antes da operação. Faça a operação em janela controlada e valide login, banco, anexos e SMTP depois.

### 11.4 Retenção

Backups excedentes à quantidade configurada são marcados como pruned e removidos do volume. Mantenha uma cópia externa ou offline para recuperação de desastre.

## 12. Erros, logs e auditoria

Erros não tratados tentam ser gravados:

1. em system_error_logs;
2. no arquivo LOG_ROOT/service_desk_errors.log;
3. com mascaramento de campos sensíveis.

~~~powershell
docker-compose exec web flask errors
docker-compose exec web flask errors --all
docker-compose exec web flask errors --source file
docker-compose exec web flask errors-export
docker-compose exec web flask errors-export --all --output /srv/service-desk/logs/errors.csv
~~~

A auditoria registra entidade, ação, usuário, IP e snapshots before/after quando fornecidos.

## 13. Testes e qualidade

Execute localmente:

~~~powershell
.venv\Scripts\python.exe -m pytest -q
~~~

Ou no container:

~~~powershell
docker-compose exec web python -m pytest -q
~~~

O pytest.ini aponta tests/ e adiciona a raiz do projeto ao pythonpath.

A suíte cobre dashboard, filtros, Kanban, relatórios, categorias, campos dinâmicos, anexos, workflow, SLA, transferência, e-mail, segurança, backup, erros e auditoria.

Antes de alterar uma regra, atualize ou crie um teste que demonstre o comportamento esperado.

## 14. Procedimento de manutenção

### 14.1 Alteração visual

1. localizar o template Jinja;
2. revisar classes compartilhadas em app/static/css/app.css;
3. preservar nomes de campos, endpoints, CSRF e variáveis;
4. testar desktop e tela estreita;
5. executar git diff --check e os testes.

### 14.2 Alteração de negócio

1. localizar rota e serviço;
2. validar permissão no backend;
3. revisar histórico, auditoria, notificações e SLA;
4. atualizar testes;
5. fazer backup;
6. documentar rollback.

### 14.3 Alteração de banco

1. criar backup completo;
2. avaliar dados existentes;
3. preparar passo idempotente ou migration explícita;
4. testar em cópia;
5. executar init-db somente depois;
6. validar contagens e relacionamentos.

### 14.4 Publicação

~~~powershell
git status
git add -A
git diff --cached --check
git commit -m "Descreve a alteração"
git push origin main
docker-compose up -d --build
docker-compose exec web flask init-db
docker-compose exec web python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
~~~

Se o Git acusar dubious ownership:

~~~powershell
git -c safe.directory="C:/Users/Usuário/Documents/Codex/2026-08-14/esta/service-desk-v2-1" push origin main
~~~

Não usar git reset --hard, git checkout -- ou docker-compose down -v como tentativa de correção sem confirmar o impacto e possuir backup.

## 15. Diagnóstico rápido

| Sintoma | Verificações |
|---|---|
| tela não abre | docker-compose ps, logs web e /health |
| health falha | logs db, status do PostgreSQL e DATABASE_URL |
| alteração não aparece | rebuild e recarga sem cache |
| login falha | usuário ativo, e-mail, perfil e SECRET_KEY |
| categoria não salva | categoria ativa, campo selecionado e erros |
| anexo não baixa | volume uploads, extensão e caminho |
| anexo sumiu | deleted_at e volume de uploads |
| e-mail falha | mail_config.json e diagnose_email.py |
| backup falha | espaço, pg_dump, logs e backup-test |
| erro 413 | MAX_UPLOAD_MB e limite do proxy |
| usuário não vê demanda | perfil, solicitante e responsável |
| botão não aparece | status e permissão backend |

## 16. Limitações atuais

- não há serviço interno de agendamento; backup automático depende de agendador externo;
- init-db é um mecanismo leve, não substitui migrations versionadas;
- SMTP é arquivo local persistido em volume;
- e-mail é enviado de forma síncrona, protegido por exceções;
- PostgreSQL é o banco oficial do Compose; SQLite é para testes/desenvolvimento;
- não expor diretamente à internet sem HTTPS, gestão de segredos, backups externos e revisão de permissões.

## 17. Checklist de entrega

- [ ] comportamento documentado;
- [ ] permissão revisada no backend;
- [ ] impacto em histórico, auditoria, SLA e notificações avaliado;
- [ ] backup feito antes de alteração de dados;
- [ ] testes atualizados e passando;
- [ ] git diff --check sem problemas;
- [ ] .env, SMTP, backups e anexos fora do commit;
- [ ] Docker reconstruído e /health validado;
- [ ] rollback conhecido.

## 18. Glossário

- **Demanda:** registro de atendimento representado por Ticket.
- **Solicitante:** usuário que abriu a demanda.
- **Responsável:** usuário que assumiu ou recebeu a demanda.
- **SLA:** prazo e tempo ativo de atendimento.
- **Pausa:** período descontado do tempo ativo e acrescentado ao prazo ao retomar.
- **Categoria:** classificação que pode definir campos dinâmicos.
- **Anexo inicial:** arquivo enviado na criação ou edição aberta.
- **Arquivo final:** arquivo enviado na conclusão.
- **Notificação interna:** aviso persistido no banco.
- **Evento de e-mail:** tipo de mudança escolhido pelo usuário.
- **Auditoria:** registro técnico de ação, usuário, entidade e data.
- **BackupRun:** registro de backup ou restauração.
