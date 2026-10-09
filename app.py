from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from json import JSONDecodeError, dumps, load
from os import environ
from pathlib import Path
from secrets import token_hex
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Uuid
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError

load_dotenv()

app = Flask(__name__)
app.secret_key = environ.get("FLASK_SECRET_KEY") or token_hex(32)
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=environ.get("SESSION_COOKIE_SECURE", "false").casefold() == "true",
)
csrf = CSRFProtect(app)
database_url = environ.get("DATABASE_URL", "sqlite:///st-transportes.db").strip()
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
if database_url.startswith("postgresql+"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "connect_args": {"sslmode": "require"},
    }
else:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False},
    }

db = SQLAlchemy(app)


class RouteRecord(db.Model):
    __tablename__ = "routes"

    id = db.Column(db.Integer, primary_key=True)
    destino = db.Column(db.String(180), nullable=False, unique=True)
    valor_por_tonelada = db.Column(db.Numeric(12, 2), nullable=False)

    def as_dict(self):
        return {
            "id": self.id,
            "destino": self.destino,
            "valor_por_tonelada": self.valor_por_tonelada,
        }


class TripRecord(db.Model):
    __tablename__ = "trips"

    id = db.Column(db.Integer, primary_key=True)
    motorista_user_id = db.Column(Uuid(as_uuid=False), nullable=True, index=True)
    rota_id = db.Column(db.Integer, db.ForeignKey("routes.id", ondelete="RESTRICT"), nullable=False)
    destino = db.Column(db.String(180), nullable=False)
    toneladas = db.Column(db.Numeric(12, 2), nullable=False)
    valor_por_tonelada = db.Column(db.Numeric(12, 2), nullable=False)
    valor_frete = db.Column(db.Numeric(12, 2), nullable=False)
    km_inicial = db.Column(db.Float, nullable=False)
    data_inicio = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    veiculo = db.Column(db.String(100), nullable=False)
    km_final = db.Column(db.Float)
    data_final = db.Column(db.DateTime)
    litros = db.Column(db.Numeric(12, 2))
    preco_litro = db.Column(db.Numeric(12, 2))
    valor_abastecimento = db.Column(db.Numeric(12, 2))
    km_por_litro = db.Column(db.Float)

    def as_dict(self):
        return {
            "id": self.id,
            "rota_id": self.rota_id,
            "destino": self.destino,
            "toneladas": self.toneladas,
            "valor_por_tonelada": self.valor_por_tonelada,
            "valor_frete": self.valor_frete,
            "km_inicial": self.km_inicial,
            "data_inicio": self.data_inicio,
            "status": self.status,
            "veiculo": self.veiculo,
            "km_final": self.km_final,
            "data_final": self.data_final,
            "litros": self.litros,
            "preco_litro": self.preco_litro,
            "valor_abastecimento": self.valor_abastecimento,
            "km_por_litro": self.km_por_litro,
        }


class UserProfile(db.Model):
    __tablename__ = "user_profiles"
    __table_args__ = (
        db.CheckConstraint("role IN ('gestor', 'motorista')", name="ck_user_profiles_role"),
    )

    user_id = db.Column(Uuid(as_uuid=False), primary_key=True)
    email = db.Column(db.String(320), nullable=False, unique=True)
    role = db.Column(db.String(20), nullable=False)


def now_datetime_local():
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def format_datetime(value):
    if not value:
        return "—"
    return value.strftime("%d/%m/%Y %H:%M")


def format_brl(value):
    whole, cents = f"{value:,.2f}".split(".")
    return f"R$ {whole.replace(',', '.')},{cents}"


def get_active_trip(motorista_user_id):
    return (
        TripRecord.query.filter_by(status="em_andamento", motorista_user_id=motorista_user_id)
        .order_by(TripRecord.id.desc())
        .first()
    )


def find_trip(trip_id):
    return db.session.get(TripRecord, trip_id)


def find_route(route_id):
    return db.session.get(RouteRecord, route_id)


def authenticate_supabase_user(email, password):
    supabase_url = environ.get("SUPABASE_URL", "").strip().rstrip("/")
    anon_key = environ.get("SUPABASE_ANON_KEY", "").strip()
    if not supabase_url or not anon_key:
        raise RuntimeError("A autenticação Supabase ainda não foi configurada.")

    payload = dumps({"email": email, "password": password}).encode("utf-8")
    auth_request = Request(
        f"{supabase_url}/auth/v1/token?grant_type=password",
        data=payload,
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(auth_request, timeout=12) as response:
            auth_result = load(response)
    except HTTPError as error:
        if error.code in {400, 401, 403}:
            return None
        raise ConnectionError("O serviço de autenticação está indisponível.") from None
    except (URLError, TimeoutError, JSONDecodeError):
        raise ConnectionError("O serviço de autenticação está indisponível.") from None

    user = auth_result.get("user")
    if not user or not user.get("id") or not user.get("email"):
        return None
    return user


def require_role(*allowed_roles):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user_id = session.get("user_id")
            role = session.get("role")
            if not user_id or role not in {"gestor", "motorista"}:
                session.clear()
                flash("Entre com sua conta para continuar.", "error")
                return redirect(url_for("login"))
            if allowed_roles and role not in allowed_roles:
                flash("Seu perfil não tem acesso a essa área.", "error")
                destination = "gestor" if role == "gestor" else "motorista_viagens"
                return redirect(url_for(destination))
            return view(*args, **kwargs)

        return wrapped

    return decorate


@app.errorhandler(CSRFError)
def handle_csrf_error(_error):
    flash("O formulário expirou. Tente novamente.", "error")
    return redirect(url_for("login"))


@app.route("/")
def login():
    if session.get("user_id"):
        return redirect(url_for("gestor" if session.get("role") == "gestor" else "motorista_viagens"))
    return render_template("login.html")


@app.route("/login", methods=["GET", "POST"])
def do_login():
    if request.method == "GET":
        return redirect(url_for("login"))

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not email or not password:
        flash("Informe seu e-mail e senha para continuar.", "error")
        return redirect(url_for("login"))

    try:
        auth_user = authenticate_supabase_user(email, password)
    except RuntimeError as error:
        flash(str(error), "error")
        return redirect(url_for("login"))
    except ConnectionError:
        flash("Não foi possível conectar ao serviço de login. Tente novamente.", "error")
        return redirect(url_for("login"))

    if auth_user is None:
        flash("E-mail ou senha inválidos.", "error")
        return redirect(url_for("login"))

    profile = db.session.get(UserProfile, auth_user["id"])
    if profile is None or profile.email.casefold() != auth_user["email"].casefold():
        flash("Sua conta ainda não tem um perfil liberado. Procure o Gestor.", "error")
        return redirect(url_for("login"))

    session.clear()
    session.permanent = True
    session["user_id"] = auth_user["id"]
    session["email"] = auth_user["email"]
    session["role"] = profile.role
    flash("Login realizado com sucesso!", "success")
    return redirect(url_for("gestor" if profile.role == "gestor" else "motorista_viagens"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Sessão encerrada.", "success")
    return redirect(url_for("login"))


@app.route("/motorista")
@require_role("motorista")
def motorista():
    return redirect(url_for("motorista_viagens"))


@app.route("/motorista/viagens")
@require_role("motorista")
def motorista_viagens():
    user_id = session["user_id"]
    return render_template(
        "motorista.html",
        trips=[
            trip.as_dict()
            for trip in TripRecord.query.filter_by(motorista_user_id=user_id).order_by(TripRecord.id).all()
        ],
        routes=[route.as_dict() for route in RouteRecord.query.order_by(RouteRecord.id).all()],
        active_trip=get_active_trip(user_id),
        now_value=now_datetime_local(),
        format_datetime=format_datetime,
        format_brl=format_brl,
        view="viagens",
    )


@app.route("/motorista/nova", methods=["POST"])
@require_role("motorista")
def nova_viagem():
    route_id = request.form.get("rota_id", "").strip()
    toneladas_value = request.form.get("toneladas", "").strip()
    km_inicial = request.form.get("km_inicial", "").strip()
    data_inicial = request.form.get("data_inicial") or now_datetime_local()
    route = find_route(int(route_id)) if route_id.isdigit() else None

    if route is None:
        flash("Selecione uma rota cadastrada pelo Gestor.", "error")
        return redirect(url_for("motorista_viagens"))
    if not toneladas_value or not km_inicial:
        flash("Informe a tonelagem e o KM inicial para abrir a viagem.", "error")
        return redirect(url_for("motorista_viagens"))

    try:
        toneladas = Decimal(toneladas_value)
        km_inicial_value = float(km_inicial)
        data_inicio = datetime.strptime(data_inicial, "%Y-%m-%dT%H:%M")
    except (InvalidOperation, ValueError):
        flash("Confira a tonelagem, o KM inicial e a data informados.", "error")
        return redirect(url_for("motorista_viagens"))

    if not toneladas.is_finite() or toneladas <= 0 or km_inicial_value < 0:
        flash("A tonelagem deve ser maior que zero e o KM não pode ser negativo.", "error")
        return redirect(url_for("motorista_viagens"))

    valor_frete = (route.valor_por_tonelada * toneladas).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    trip = TripRecord(
        motorista_user_id=session["user_id"],
        rota_id=route.id,
        destino=route.destino,
        toneladas=toneladas,
        valor_por_tonelada=route.valor_por_tonelada,
        valor_frete=valor_frete,
        km_inicial=km_inicial_value,
        data_inicio=data_inicio,
        status="em_andamento",
        veiculo="Veículo 12",
    )
    db.session.add(trip)
    db.session.commit()
    flash("Viagem aberta com sucesso!", "success")
    return redirect(url_for("motorista_viagens"))


@app.route("/motorista/finalizar/<int:trip_id>", methods=["POST"])
@require_role("motorista")
def finalizar_viagem(trip_id):
    trip = find_trip(trip_id)

    if trip is None or trip.motorista_user_id != session["user_id"]:
        flash("Viagem não encontrada.", "error")
        return redirect(url_for("motorista_viagens"))

    km_final = request.form.get("km_final", "").strip()
    litros = request.form.get("litros", "").strip()
    preco_litro = request.form.get("preco_litro", "").strip()

    if not km_final or not litros or not preco_litro:
        flash("Informe KM final, litros e valor por litro.", "error")
        return redirect(url_for("motorista_viagens"))

    try:
        km_final_value = float(km_final)
        liters_value = Decimal(litros)
        price_per_liter = Decimal(preco_litro)
    except (InvalidOperation, ValueError):
        flash("Confira o KM final, os litros e o valor por litro.", "error")
        return redirect(url_for("motorista_viagens"))

    if (
        not liters_value.is_finite()
        or not price_per_liter.is_finite()
        or km_final_value < trip.km_inicial
        or liters_value <= 0
        or price_per_liter <= 0
    ):
        flash("O KM final deve ser maior que o inicial e os valores do abastecimento devem ser positivos.", "error")
        return redirect(url_for("motorista_viagens"))

    trip.km_final = km_final_value
    trip.data_final = datetime.now()
    trip.litros = liters_value
    trip.preco_litro = price_per_liter
    trip.valor_abastecimento = (liters_value * price_per_liter).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    trip.km_por_litro = (km_final_value - trip.km_inicial) / float(liters_value)
    trip.status = "finalizada"
    db.session.commit()

    flash("Viagem finalizada com sucesso!", "success")
    return redirect(url_for("motorista_viagens"))


@app.route("/dashboard")
@require_role("gestor")
def dashboard():
    return redirect(url_for("gestor"))


@app.route("/gestor")
@require_role("gestor")
def gestor():
    trip_records = TripRecord.query.order_by(TripRecord.id.desc()).all()
    trips = [trip.as_dict() for trip in trip_records]
    route_records = RouteRecord.query.order_by(RouteRecord.id).all()
    completed_trips = [trip for trip in trips if trip["status"] == "finalizada"]
    return render_template(
        "gestor.html",
        trips=trips,
        routes=[route.as_dict() for route in route_records],
        total_trips=len(trips),
        active_trips=sum(trip["status"] == "em_andamento" for trip in trips),
        completed_trips=len(completed_trips),
        total_fuel=sum(trip["valor_abastecimento"] or 0 for trip in trips),
        total_liters=sum(trip["litros"] or 0 for trip in trips),
        total_freight=sum((trip.get("valor_frete") or Decimal("0.00") for trip in trips), Decimal("0.00")),
        format_datetime=format_datetime,
        format_brl=format_brl,
    )


@app.route("/gestor/abastecimento")
@require_role("gestor")
def gestor_abastecimento():
    trip_records = TripRecord.query.filter_by(status="finalizada").order_by(TripRecord.data_final.desc()).all()
    fuel_entries = [
        trip.as_dict() for trip in trip_records
        if trip.litros is not None
        and trip.valor_abastecimento is not None
    ]
    total_fuel = sum(trip["valor_abastecimento"] for trip in fuel_entries)
    total_liters = sum(trip["litros"] for trip in fuel_entries)
    stats_by_route = {}
    for trip in fuel_entries:
        route_id = trip.get("rota_id")
        route_key = route_id if route_id is not None else trip["destino"]
        route_stats = stats_by_route.setdefault(
            route_key,
            {"destino": trip["destino"], "viagens": 0, "km_total": 0.0, "litros": Decimal("0"), "valor_total": Decimal("0")},
        )
        route_stats["viagens"] += 1
        route_stats["km_total"] += max(trip["km_final"] - trip["km_inicial"], 0)
        route_stats["litros"] += trip["litros"]
        route_stats["valor_total"] += trip["valor_abastecimento"]

    route_averages = [
        {
            **stats,
            "km_por_litro": stats["km_total"] / float(stats["litros"]) if stats["litros"] else 0,
        }
        for stats in stats_by_route.values()
    ]
    return render_template(
        "gestor_abastecimento.html",
        fuel_entries=sorted(fuel_entries, key=lambda trip: trip["data_final"], reverse=True),
        total_entries=len(fuel_entries),
        total_fuel=total_fuel,
        total_liters=total_liters,
        average_price=total_fuel / total_liters if total_liters else 0,
        route_averages=sorted(route_averages, key=lambda stats: stats["destino"].casefold()),
        format_datetime=format_datetime,
        format_brl=format_brl,
    )


@app.route("/gestor/viagens/<int:trip_id>/editar", methods=["POST"])
@require_role("gestor")
def editar_viagem(trip_id):
    trip = find_trip(trip_id)
    if trip is None:
        flash("Viagem não encontrada.", "error")
        return redirect(url_for("gestor"))

    route_id = request.form.get("rota_id", "").strip()
    route = find_route(int(route_id)) if route_id.isdigit() else None
    veiculo = request.form.get("veiculo", "").strip()
    data_inicial = request.form.get("data_inicial", "").strip()
    km_inicial = request.form.get("km_inicial", "").strip()
    toneladas_value = request.form.get("toneladas", "").strip()
    status = request.form.get("status", "").strip()
    km_final = request.form.get("km_final", "").strip()
    data_final = request.form.get("data_final", "").strip()
    litros = request.form.get("litros", "").strip()
    preco_litro = request.form.get("preco_litro", "").strip()

    if route is None or not veiculo or not data_inicial or not km_inicial or not toneladas_value:
        flash("Preencha rota, veículo, data inicial, KM inicial e tonelagem.", "error")
        return redirect(url_for("gestor"))
    if status not in {"em_andamento", "finalizada"}:
        flash("Selecione um status válido para a viagem.", "error")
        return redirect(url_for("gestor"))
    if status == "finalizada" and not all((km_final, data_final, litros, preco_litro)):
        flash("Para finalizar, informe data, KM final, litros e valor por litro.", "error")
        return redirect(url_for("gestor"))

    try:
        start_date = datetime.strptime(data_inicial, "%Y-%m-%dT%H:%M")
        start_km = float(km_inicial)
        toneladas = Decimal(toneladas_value)
        end_km = float(km_final) if status == "finalizada" else None
        end_date = datetime.strptime(data_final, "%Y-%m-%dT%H:%M") if status == "finalizada" else None
        fuel_liters = Decimal(litros) if status == "finalizada" else None
        fuel_price = Decimal(preco_litro) if status == "finalizada" else None
    except (InvalidOperation, ValueError):
        flash("Confira os valores numéricos e as datas informadas.", "error")
        return redirect(url_for("gestor"))

    if not toneladas.is_finite() or toneladas <= 0:
        flash("A tonelagem deve ser maior que zero.", "error")
        return redirect(url_for("gestor"))
    if min(start_km, end_km or 0, float(fuel_liters or 0), float(fuel_price or 0)) < 0:
        flash("Os valores numéricos não podem ser negativos.", "error")
        return redirect(url_for("gestor"))
    if end_km is not None and end_km < start_km:
        flash("O KM final não pode ser menor que o KM inicial.", "error")
        return redirect(url_for("gestor"))
    if status == "finalizada" and (fuel_liters <= 0 or fuel_price <= 0):
        flash("Litros e preço por litro devem ser maiores que zero.", "error")
        return redirect(url_for("gestor"))

    fuel_value = (fuel_liters * fuel_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if status == "finalizada" else None
    km_per_liter = (end_km - start_km) / float(fuel_liters) if status == "finalizada" else None

    trip.rota_id = route.id
    trip.destino = route.destino
    trip.toneladas = toneladas
    trip.valor_por_tonelada = route.valor_por_tonelada
    trip.valor_frete = (route.valor_por_tonelada * toneladas).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    trip.veiculo = veiculo
    trip.data_inicio = start_date
    trip.km_inicial = start_km
    trip.status = status
    trip.km_final = end_km
    trip.data_final = end_date
    trip.litros = fuel_liters
    trip.preco_litro = fuel_price
    trip.valor_abastecimento = fuel_value
    trip.km_por_litro = km_per_liter
    db.session.commit()
    flash("Viagem atualizada com sucesso.", "success")
    return redirect(url_for("gestor"))


@app.route("/gestor/viagens/<int:trip_id>/excluir", methods=["POST"])
@require_role("gestor")
def excluir_viagem(trip_id):
    trip = find_trip(trip_id)
    if trip is None:
        flash("Viagem não encontrada.", "error")
        return redirect(url_for("gestor"))

    db.session.delete(trip)
    db.session.commit()
    flash("Viagem excluída com sucesso.", "success")
    return redirect(url_for("gestor"))


@app.route("/gestor/rotas")
@require_role("gestor")
def gestor_rotas():
    route_records = RouteRecord.query.order_by(RouteRecord.id).all()
    return render_template(
        "gestor_rotas.html",
        routes=[route.as_dict() for route in route_records],
        format_brl=format_brl,
        trips_by_route={
            route.id: TripRecord.query.filter_by(rota_id=route.id).count()
            for route in route_records
        },
    )


@app.route("/gestor/rotas/nova", methods=["POST"])
@require_role("gestor")
def nova_rota():
    destino = request.form.get("destino", "").strip()
    valor_value = request.form.get("valor_por_tonelada", "").strip()
    if not destino or not valor_value:
        flash("Informe o destino da rota e o frete por tonelada.", "error")
        return redirect(url_for("gestor_rotas"))

    try:
        valor_por_tonelada = Decimal(valor_value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        flash("Informe um valor de frete válido.", "error")
        return redirect(url_for("gestor_rotas"))

    if not valor_por_tonelada.is_finite() or valor_por_tonelada <= 0:
        flash("O frete por tonelada deve ser maior que zero.", "error")
        return redirect(url_for("gestor_rotas"))
    if any(route.destino.casefold() == destino.casefold() for route in RouteRecord.query.all()):
        flash("Já existe uma rota cadastrada com esse destino.", "error")
        return redirect(url_for("gestor_rotas"))

    route = RouteRecord(destino=destino, valor_por_tonelada=valor_por_tonelada)
    db.session.add(route)
    db.session.commit()
    flash("Rota cadastrada com sucesso.", "success")
    return redirect(url_for("gestor_rotas"))


@app.route("/gestor/rotas/<int:route_id>/editar", methods=["POST"])
@require_role("gestor")
def editar_rota(route_id):
    route = find_route(route_id)
    if route is None:
        flash("Rota não encontrada.", "error")
        return redirect(url_for("gestor_rotas"))

    destino = request.form.get("destino", "").strip()
    valor_value = request.form.get("valor_por_tonelada", "").strip()
    if not destino or not valor_value:
        flash("Informe o destino e o frete por tonelada.", "error")
        return redirect(url_for("gestor_rotas"))

    try:
        valor_por_tonelada = Decimal(valor_value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        flash("Informe um valor de frete válido.", "error")
        return redirect(url_for("gestor_rotas"))

    if not valor_por_tonelada.is_finite() or valor_por_tonelada <= 0:
        flash("O frete por tonelada deve ser maior que zero.", "error")
        return redirect(url_for("gestor_rotas"))
    if any(
        other.id != route_id and other.destino.casefold() == destino.casefold()
        for other in RouteRecord.query.all()
    ):
        flash("Já existe outra rota cadastrada com esse destino.", "error")
        return redirect(url_for("gestor_rotas"))

    route.destino = destino
    route.valor_por_tonelada = valor_por_tonelada
    db.session.commit()
    flash("Rota atualizada com sucesso. Fretes já calculados foram mantidos nas viagens existentes.", "success")
    return redirect(url_for("gestor_rotas"))


@app.route("/gestor/rotas/<int:route_id>/excluir", methods=["POST"])
@require_role("gestor")
def excluir_rota(route_id):
    route = find_route(route_id)
    if route is None:
        flash("Rota não encontrada.", "error")
        return redirect(url_for("gestor_rotas"))
    if TripRecord.query.filter_by(rota_id=route_id).first() is not None:
        flash("Esta rota está vinculada a uma viagem. Exclua ou atualize a viagem antes.", "error")
        return redirect(url_for("gestor_rotas"))

    db.session.delete(route)
    db.session.commit()
    flash("Rota excluída com sucesso.", "success")
    return redirect(url_for("gestor_rotas"))


Path(app.instance_path).mkdir(parents=True, exist_ok=True)
with app.app_context():
    db.create_all()
    with db.engine.begin() as connection:
        if db.engine.dialect.name == "postgresql":
            for table_name in ("routes", "trips", "user_profiles"):
                connection.exec_driver_sql(
                    f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY"
                )
                connection.exec_driver_sql(
                    f"REVOKE ALL ON TABLE public.{table_name} FROM anon, authenticated"
                )
        if db.engine.dialect.name == "sqlite":
            trip_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(trips)")}
            if "motorista_user_id" not in trip_columns:
                connection.exec_driver_sql("ALTER TABLE trips ADD COLUMN motorista_user_id CHAR(32)")
        else:
            connection.exec_driver_sql("ALTER TABLE trips ADD COLUMN IF NOT EXISTS motorista_user_id UUID")
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_trips_motorista_user_id ON trips (motorista_user_id)"
        )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
