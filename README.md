# Service Desk V2.1

Nova base do Service Desk, criada em pasta separada para substituir gradualmente a V2.

## Decisoes principais

- Flask com application factory e blueprints.
- CSRF habilitado em todos os formularios.
- Regras de permissao aplicadas no backend, nao apenas na tela.
- Uploads fora do codigo-fonte e protegidos contra path traversal.
- Docker com Gunicorn na porta `18437` para teste local.
- PostgreSQL em volume separado no Docker.
- Sem segredos versionados.

## Rodar com Docker

1. Copie `.env.example` para `.env`.
2. Defina `SECRET_KEY` e `POSTGRES_PASSWORD`.
3. Execute:

```powershell
docker-compose up --build
```

A aplicacao ficara em `http://localhost:18437`.

## Inicializar banco

Depois que os containers estiverem no ar:

```powershell
docker-compose exec web flask init-db
docker-compose exec web flask seed-admin --email admin@empresa.com.br --password "troque-esta-senha"

```

Diagnóstico de e-mail:

```bash
docker-compose exec web python scripts/diagnose_email.py
docker-compose exec web python scripts/diagnose_email.py --send-test --recipient seu-email@empresa.com.br
```

Sempre que uma nova tabela ou coluna for adicionada durante o desenvolvimento, rode novamente:

```powershell
docker-compose exec web flask init-db
```

Esse comando cria tabelas ausentes sem apagar os dados existentes.

## Consultar erros sem abrir o sistema

Se a tela nao abrir, consulte os erros pelo terminal:

```powershell
docker-compose exec web flask errors
```

Se o banco estiver indisponivel, leia o arquivo de fallback:

```powershell
docker-compose exec web flask errors --source file
```

Para exportar os erros do banco para CSV dentro do container:

```powershell
docker-compose exec web flask errors-export
```

O arquivo de fallback fica em `/srv/service-desk/logs/service_desk_errors.log`.

## Backup e restauracao

A tela administrativa `Backup` permite configurar horarios, quantidade a manter, inclusao de anexos/logs, gerar backup manual e restaurar com confirmacao.

Comandos de emergencia:

```powershell
docker-compose exec web flask backup-create
docker-compose exec web flask backup-list
docker-compose exec web flask backup-test --id ID
docker-compose exec web flask backup-restore --id ID --confirm RESTAURAR
```

Para agendamento automatico, use o Agendador de Tarefas do Windows chamando `docker-compose exec web flask backup-create` nos horarios configurados na tela. Os arquivos ficam no volume Docker montado em `/srv/service-desk/backups`.

## Testes locais

```powershell
pytest
```
