from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, MultipleFileField
from wtforms import BooleanField, DateField, EmailField, HiddenField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, Optional


class LoginForm(FlaskForm):
    email = EmailField("E-mail", validators=[DataRequired(), Email()])
    password = PasswordField("Senha", validators=[DataRequired()])
    submit = SubmitField("Entrar")


class ProfilePasswordForm(FlaskForm):
    current_password = PasswordField("Senha atual", validators=[DataRequired()])
    new_password = PasswordField("Nova senha", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField("Confirmar senha", validators=[DataRequired(), EqualTo("new_password")])
    submit = SubmitField("Alterar senha")


class EmailNotificationPreferencesForm(FlaskForm):
    email_enabled = BooleanField("Receber avisos por e-mail")
    pausada = BooleanField("Quando uma demanda for pausada")
    concluida = BooleanField("Quando uma demanda for concluída")
    reaberta = BooleanField("Quando uma demanda for reaberta")
    cancelada = BooleanField("Quando uma demanda for cancelada")
    comentario = BooleanField("Quando houver um novo comentário")
    submit = SubmitField("Salvar preferências")


class MailSettingsForm(FlaskForm):
    server = StringField("Servidor SMTP", validators=[Optional(), Length(max=200)])
    port = IntegerField("Porta", validators=[Optional(), NumberRange(min=1, max=65535)])
    username = StringField("Usuário SMTP", validators=[Optional(), Length(max=200)])
    password = PasswordField("Senha SMTP", validators=[Optional(), Length(max=300)])
    sender = EmailField("Remetente padrão", validators=[Optional(), Email()])
    test_recipient = EmailField("Destinatário do teste", validators=[Optional(), Email()])
    use_tls = BooleanField("Usar TLS")
    use_ssl = BooleanField("Usar SSL")
    save = SubmitField("Apenas salvar")
    test = SubmitField("Testar conexão de e-mail")


class TicketForm(FlaskForm):
    title = StringField("Titulo", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Descricao", validators=[DataRequired()])
    priority = SelectField(
        "Prioridade",
        choices=[("Baixa", "Baixa"), ("Media", "Media"), ("Alta", "Alta"), ("Urgente", "Urgente")],
        validators=[DataRequired()],
    )
    category_id = SelectField("Categoria", coerce=int, validators=[DataRequired()])
    branch_id = SelectField("Filial", coerce=int, validators=[Optional()])
    due_at = DateField("Prazo SLA", validators=[DataRequired()])
    initial_files = MultipleFileField(
        "Anexos iniciais",
        validators=[FileAllowed(["txt", "pdf", "png", "jpg", "jpeg", "csv", "xlsx", "xls", "doc", "docx", "xml", "ppt", "pptx"])],
    )
    submit = SubmitField("Salvar solicitacao")


class TicketActionForm(FlaskForm):
    action = HiddenField("Acao", validators=[DataRequired()])
    note = TextAreaField("Observacao", validators=[Optional(), Length(max=4000)])
    final_files = MultipleFileField(
        "Arquivos finais",
        validators=[FileAllowed(["txt", "pdf", "png", "jpg", "jpeg", "csv", "xlsx", "xls", "doc", "docx", "xml", "ppt", "pptx"])],
    )
    submit = SubmitField("Confirmar")


class TicketTransferForm(FlaskForm):
    assignee_id = SelectField("Novo responsavel", coerce=int, validators=[DataRequired()])
    note = TextAreaField("Observacao", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Transferir")


class CommentForm(FlaskForm):
    body = StringField("Comentario", validators=[DataRequired(), Length(max=1000)])
    submit = SubmitField("Enviar")


class UserForm(FlaskForm):
    name = StringField("Nome", validators=[DataRequired(), Length(max=120)])
    email = EmailField("E-mail", validators=[DataRequired(), Email()])
    password = PasswordField("Senha inicial", validators=[Optional(), Length(min=8)])
    profile_id = SelectField("Perfil", coerce=int, validators=[DataRequired()])
    active = BooleanField("Ativo", default=True)
    submit = SubmitField("Salvar usuario")


class UserEditForm(FlaskForm):
    name = StringField("Nome", validators=[DataRequired(), Length(max=120)])
    email = EmailField("E-mail", validators=[DataRequired(), Email()])
    profile_id = SelectField("Perfil", coerce=int, validators=[DataRequired()])
    active = BooleanField("Ativo")
    submit = SubmitField("Salvar alteracoes")


class AdminPasswordResetForm(FlaskForm):
    new_password = PasswordField("Nova senha", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField("Confirmar senha", validators=[DataRequired(), EqualTo("new_password")])
    submit = SubmitField("Redefinir senha")


class AccessProfileForm(FlaskForm):
    name = StringField("Nome", validators=[DataRequired(), Length(max=100)])
    can_manage_users = BooleanField("Gerir usuarios")
    can_manage_settings = BooleanField("Gerir parametros")
    can_reset_data = BooleanField("Operacoes criticas")
    can_work_tickets = BooleanField("Trabalhar solicitacoes")
    can_view_reports = BooleanField("Ver relatorios")
    submit = SubmitField("Salvar perfil")


class CategoryForm(FlaskForm):
    name = StringField("Nome", validators=[DataRequired(), Length(max=120)])
    active = BooleanField("Ativa", default=True)
    submit = SubmitField("Salvar categoria")


class BranchForm(FlaskForm):
    name = StringField("Nome", validators=[DataRequired(), Length(max=120)])
    kind = StringField("Tipo", validators=[DataRequired(), Length(max=60)])
    active = BooleanField("Ativa", default=True)
    submit = SubmitField("Salvar filial")


class BackupSettingsForm(FlaskForm):
    enabled = BooleanField("Backup agendado ativo")
    schedule_times = StringField("Horarios", validators=[DataRequired(), Length(max=200)])
    max_backup_count = IntegerField("Quantidade a manter", validators=[DataRequired(), NumberRange(min=1, max=365)])
    include_uploads = BooleanField("Incluir anexos", default=True)
    include_logs = BooleanField("Incluir logs", default=True)
    submit = SubmitField("Salvar configuracao")


class BackupRestoreForm(FlaskForm):
    backup_id = HiddenField("Backup", validators=[DataRequired()])
    confirmation = StringField("Confirmacao", validators=[DataRequired(), Length(max=20)])
    submit = SubmitField("Restaurar")
