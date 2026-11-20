from flask import Blueprint, request, jsonify
from app.pipeline import run
from app.errors import DomainError
from app import store

bp = Blueprint("m8", __name__)


def _payload():
    data = request.get_json(silent=True) if request.method == "POST" else {}
    if not isinstance(data, dict):
        raise DomainError("BODY", "请求必须是对象")
    raw = data.get("text")
    if raw is not None and not isinstance(raw, str):
        raise DomainError("TEXT", "数据必须为文本")
    return raw, data.get("params")


@bp.route("/api/m8", methods=["GET", "POST"])
def endpoint():
    raw, params = _payload()
    return jsonify(run(raw, params, stop=8, persist=False))


@bp.route("/api/admission", methods=["GET", "POST"])
def admission():
    raw, params = _payload()
    result = run(raw, params, stop=8, persist=True)
    return jsonify(store.save_admission(result))
