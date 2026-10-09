from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from os import environ
from secrets import token_hex

from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = environ.get("FLASK_SECRET_KEY") or token_hex(32)

trips = []
routes = []


def now_datetime_local():
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def format_datetime(value):
    if not value:
        return "—"
    return value.strftime("%d/%m/%Y %H:%M")


def format_brl(value):
    whole, cents = f"{value:,.2f}".split(".")
    return f"R$ {whole.replace(',', '.')},{cents}"


def get_active_trip():
    return next((trip for trip in reversed(trips) if trip["status"] == "em_andamento"), None)


def find_trip(trip_id):
    return next((trip for trip in trips if trip["id"] == trip_id), None)


def find_route(route_id):
    return next((route for route in routes if route["id"] == route_id), None)


@app.route("/")
def login():
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def do_login():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    perfil = request.form.get("perfil", "motorista").strip()

    if not email or not password:
        flash("Informe seu e-mail e senha para continuar.", "error")
        return redirect(url_for("login"))
    if perfil not in {"motorista", "gestor"}:
        flash("Selecione um perfil válido para continuar.", "error")
        return redirect(url_for("login"))

    flash("Login realizado com sucesso!", "success")
    return redirect(url_for("gestor" if perfil == "gestor" else "motorista"))


@app.route("/motorista")
def motorista():
    return redirect(url_for("motorista_viagens"))


@app.route("/motorista/viagens")
def motorista_viagens():
    return render_template(
        "motorista.html",
        trips=trips,
        routes=routes,
        active_trip=get_active_trip(),
        now_value=now_datetime_local(),
        format_datetime=format_datetime,
        format_brl=format_brl,
        view="viagens",
    )


@app.route("/motorista/nova", methods=["POST"])
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

    valor_frete = (route["valor_por_tonelada"] * toneladas).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    trip = {
        "id": max((item["id"] for item in trips), default=0) + 1,
        "rota_id": route["id"],
        "destino": route["destino"],
        "toneladas": toneladas,
        "valor_por_tonelada": route["valor_por_tonelada"],
        "valor_frete": valor_frete,
        "km_inicial": km_inicial_value,
        "data_inicio": data_inicio,
        "status": "em_andamento",
        "veiculo": "Veículo 12",
        "km_final": None,
        "data_final": None,
        "litros": None,
        "preco_litro": None,
        "valor_abastecimento": None,
        "km_por_litro": None,
    }
    trips.append(trip)
    flash("Viagem aberta com sucesso!", "success")
    return redirect(url_for("motorista_viagens"))


@app.route("/motorista/finalizar/<int:trip_id>", methods=["POST"])
def finalizar_viagem(trip_id):
    trip = next((item for item in trips if item["id"] == trip_id), None)

    if trip is None:
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
        or km_final_value < trip["km_inicial"]
        or liters_value <= 0
        or price_per_liter <= 0
    ):
        flash("O KM final deve ser maior que o inicial e os valores do abastecimento devem ser positivos.", "error")
        return redirect(url_for("motorista_viagens"))

    trip["km_final"] = km_final_value
    trip["data_final"] = datetime.now()
    trip["litros"] = liters_value
    trip["preco_litro"] = price_per_liter
    trip["valor_abastecimento"] = (liters_value * price_per_liter).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    trip["km_por_litro"] = (km_final_value - trip["km_inicial"]) / float(liters_value)
    trip["status"] = "finalizada"

    flash("Viagem finalizada com sucesso!", "success")
    return redirect(url_for("motorista_viagens"))


@app.route("/dashboard")
def dashboard():
    return redirect(url_for("gestor"))


@app.route("/gestor")
def gestor():
    completed_trips = [trip for trip in trips if trip["status"] == "finalizada"]
    return render_template(
        "gestor.html",
        trips=sorted(trips, key=lambda trip: trip["id"], reverse=True),
        routes=routes,
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
def gestor_abastecimento():
    fuel_entries = [
        trip for trip in trips
        if trip["status"] == "finalizada"
        and trip.get("litros") is not None
        and trip.get("valor_abastecimento") is not None
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

    trip.update(
        rota_id=route["id"],
        destino=route["destino"],
        toneladas=toneladas,
        valor_por_tonelada=route["valor_por_tonelada"],
        valor_frete=(route["valor_por_tonelada"] * toneladas).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        veiculo=veiculo,
        data_inicio=start_date,
        km_inicial=start_km,
        status=status,
        km_final=end_km,
        data_final=end_date,
        litros=fuel_liters,
        preco_litro=fuel_price,
        valor_abastecimento=fuel_value,
        km_por_litro=km_per_liter,
    )
    flash("Viagem atualizada com sucesso.", "success")
    return redirect(url_for("gestor"))


@app.route("/gestor/viagens/<int:trip_id>/excluir", methods=["POST"])
def excluir_viagem(trip_id):
    trip = find_trip(trip_id)
    if trip is None:
        flash("Viagem não encontrada.", "error")
        return redirect(url_for("gestor"))

    trips.remove(trip)
    flash("Viagem excluída com sucesso.", "success")
    return redirect(url_for("gestor"))


@app.route("/gestor/rotas")
def gestor_rotas():
    return render_template(
        "gestor_rotas.html",
        routes=sorted(routes, key=lambda route: route["id"]),
        format_brl=format_brl,
        trips_by_route={
            route["id"]: sum(trip.get("rota_id") == route["id"] for trip in trips)
            for route in routes
        },
    )


@app.route("/gestor/rotas/nova", methods=["POST"])
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
    if any(route["destino"].casefold() == destino.casefold() for route in routes):
        flash("Já existe uma rota cadastrada com esse destino.", "error")
        return redirect(url_for("gestor_rotas"))

    routes.append({
        "id": max((route["id"] for route in routes), default=0) + 1,
        "destino": destino,
        "valor_por_tonelada": valor_por_tonelada,
    })
    flash("Rota cadastrada com sucesso.", "success")
    return redirect(url_for("gestor_rotas"))


@app.route("/gestor/rotas/<int:route_id>/editar", methods=["POST"])
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
    if any(other["id"] != route_id and other["destino"].casefold() == destino.casefold() for other in routes):
        flash("Já existe outra rota cadastrada com esse destino.", "error")
        return redirect(url_for("gestor_rotas"))

    route.update(destino=destino, valor_por_tonelada=valor_por_tonelada)
    flash("Rota atualizada com sucesso. Fretes já calculados foram mantidos nas viagens existentes.", "success")
    return redirect(url_for("gestor_rotas"))


@app.route("/gestor/rotas/<int:route_id>/excluir", methods=["POST"])
def excluir_rota(route_id):
    route = find_route(route_id)
    if route is None:
        flash("Rota não encontrada.", "error")
        return redirect(url_for("gestor_rotas"))
    if any(trip.get("rota_id") == route_id for trip in trips):
        flash("Esta rota está vinculada a uma viagem. Exclua ou atualize a viagem antes.", "error")
        return redirect(url_for("gestor_rotas"))

    routes.remove(route)
    flash("Rota excluída com sucesso.", "success")
    return redirect(url_for("gestor_rotas"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
