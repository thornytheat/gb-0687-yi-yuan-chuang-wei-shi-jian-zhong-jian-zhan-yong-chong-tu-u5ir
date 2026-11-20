import pytest
from app.pipeline import run
from app.config import SAMPLES
from app.normalize import instant
from app.errors import DomainError
from app.modules.m8_admission.rules import build_board, parse_params, bed_occupants

NIGHT = (SAMPLES / "night_shift.csv").read_bytes()
SHIFT_PARAMS = {
    "shift_time": "2026-11-17T02:00:00+08:00",
    "isolation_beds": "004,104",
    "suspect_reserve": "1",
    "critical_pending": "1",
}


def board(result):
    return {b["bed"]: b for b in result["admission"]["bed_board"]}


def test_isolation_bed_not_counted_as_general_free(isolate):
    # 核心缺陷修复：02:00 仅 004 空闲且为专用隔离床，普通可收治床数必须为 0
    result = run(NIGHT, SHIFT_PARAMS, stop=8, persist=False)
    assert result["admission"]["counts"]["general_admissible"] == 0
    bed004 = board(result)["004"]
    assert bed004["isolation"] is True and bed004["reserved"] is True
    assert bed004["code"] == "ISOLATION_RESERVED"
    assert result["admission"]["counts"]["reserve_margin"] == 1
    # 不保留时隔离空床可接疑似，但仍不计入普通可收治
    no_reserve = run(NIGHT, {**SHIFT_PARAMS, "suspect_reserve": "0"}, stop=8, persist=False)
    bed004_free = board(no_reserve)["004"]
    assert bed004_free["code"] == "ISOLATION_READY" and bed004_free["admissible"] is True
    assert no_reserve["admission"]["counts"]["general_admissible"] == 0


def test_night_shift_decision_and_reserve_margin(isolate):
    # 夜班只剩一张隔离床：重症暂缓，隔离余量 1 张留给途中疑似患者
    result = run(NIGHT, SHIFT_PARAMS, stop=8, persist=False)
    assert result["admission"]["decision"] == "暂缓普通收治"
    assert result["admission"]["counts"]["critical_admitted"] == 0
    reserved = [b for b in result["admission"]["bed_board"] if b["reserved"]]
    assert [(b["bed"], b["code"]) for b in reserved] == [("004", "ISOLATION_RESERVED")]
    assert "隔离余量 1 张" in result["admission"]["summary"]


def test_general_free_bed_admits_critical_without_touching_reserve(isolate):
    # 出现普通空床时重症立即收治，隔离保留床不动
    text = NIGHT.decode() + (
        "01010,01003,003,W1,2026-11-17T01:30:00+08:00,discharge,1\n"
    )
    result = run(text, SHIFT_PARAMS, stop=8, persist=False)
    assert result["admission"]["counts"]["general_admissible"] == 1
    assert result["admission"]["decision"] == "可立即收治"
    assert result["admission"]["counts"]["critical_admitted"] == 1
    assert board(result)["004"]["code"] == "ISOLATION_RESERVED"
    assert result["admission"]["counts"]["reserve_margin"] == 1


def test_half_open_interval_discharged_at_shift_time(isolate):
    # 半开区间：01:00 已出院的 004 在 02:00 为空床
    result = run(NIGHT, SHIFT_PARAMS, stop=8, persist=False)
    assert board(result)["004"]["occupied"] is False
    assert board(result)["104"]["occupied"] is True
    occupants = bed_occupants(result["intervals"], instant(SHIFT_PARAMS["shift_time"]))
    assert "004" not in occupants and "104" in occupants


def test_every_bed_has_admissibility_and_reason(isolate):
    # 页面要求：每张床都有能否收治与原因
    result = run(NIGHT, SHIFT_PARAMS, stop=8, persist=False)
    assert len(result["admission"]["bed_board"]) == 8
    for bed in result["admission"]["bed_board"]:
        assert isinstance(bed["admissible"], bool) and bed["code"] and bed["reason"]
        assert isinstance(bed["general_admissible"], bool)
        if bed["isolation"]:
            assert bed["general_admissible"] is False
        if bed["code"] == "OCCUPIED":
            assert bed["basis"] and bed["basis"][0]["patient"]


def test_reserve_cannot_use_isolation_for_general_critical(isolate):
    # reserve=0 时隔离床仍不可收治普通患者：结论仍暂缓
    params = {**SHIFT_PARAMS, "suspect_reserve": "0"}
    result = run(NIGHT, params, stop=8, persist=False)
    assert result["admission"]["counts"]["general_admissible"] == 0
    assert result["admission"]["decision"] == "暂缓普通收治"
    assert board(result)["004"]["code"] == "ISOLATION_READY"


def test_late_isolation_record_supersedes_old_conclusion(client):
    # 晚到的隔离记录使旧结论失效，原床位去向与依据保留
    first = client.post("/api/admission", json={"text": NIGHT.decode(), "params": SHIFT_PARAMS}).json
    assert first["status"] == "现行" and first["counts"]["general_admissible"] == 0
    late_text = NIGHT.decode() + "01011,01010,004,W1,2026-11-16T23:30:00+08:00,admit,1\n"
    second = client.post("/api/admission", json={"text": late_text, "params": SHIFT_PARAMS}).json
    assert second["status"] == "现行"
    assert second["superseded_by_late"] is True
    assert second["late_isolation_triggers"][0]["bed"] == "004"
    assert second["superseded"][0]["id"] == first["admission_id"]
    old = client.get("/api/admission.csv?admission=" + first["admission_id"])
    assert old.status_code == 200 and "SUPERSEDED" in old.text
    # 旧结论的床位依据仍可读（004 当时为隔离空床，104 被占用）
    assert "OCCUPIED" in old.text and "ISOLATION_RESERVED" in old.text


def test_retry_same_record_is_idempotent(client):
    # 同一记录重试：不新增占用/不新增结论行
    a = client.post("/api/admission", json={"text": NIGHT.decode(), "params": SHIFT_PARAMS}).json
    b = client.post("/api/admission", json={"text": NIGHT.decode(), "params": SHIFT_PARAMS}).json
    assert a["admission_id"] == b["admission_id"]
    history = client.get("/api/m8").json
    page = client.get("/m/8")
    assert page.status_code == 200


def test_export_uses_same_conclusion_as_page(client):
    saved = client.post("/api/admission", json={"text": NIGHT.decode(), "params": SHIFT_PARAMS}).json
    exported = client.get("/api/admission.csv?admission=" + saved["admission_id"]).text
    assert saved["admission_id"] in exported
    assert "普通可收治床" in exported and "ISOLATION_RESERVED" in exported
    assert str(saved["counts"]["reserve_margin"]) in exported


def test_invalid_isolation_bed_rejected(isolate):
    params = {**SHIFT_PARAMS, "isolation_beds": "999"}
    with pytest.raises(DomainError) as exc:
        run(NIGHT, params, stop=8, persist=False)
    assert exc.value.code == "UNMAPPED"


def test_reserve_over_limit_rejected(isolate):
    params = {**SHIFT_PARAMS, "suspect_reserve": "3"}
    with pytest.raises(DomainError) as exc:
        run(NIGHT, params, stop=8, persist=False)
    assert exc.value.code == "LIMIT"


def test_real_sample_pipeline_includes_admission_stage(raw, isolate):
    result = run(raw, stop=8, persist=False)
    assert len(result["stages"]) == 8
    assert result["stages"][-1]["title"] == "班次收治"
    assert len(result["admission"]["bed_board"]) == 8


def test_parse_params_integral_check():
    with pytest.raises(DomainError):
        parse_params({**SHIFT_PARAMS, "suspect_reserve": "0.5"})


def test_late_quarantined_isolation_row_also_supersedes(client):
    # 隔离区中时间可解析的迟到隔离床记录同样使旧结论失效（未知事件类型 -> M2 隔离）
    first = client.post("/api/admission", json={"text": NIGHT.decode(), "params": SHIFT_PARAMS}).json
    late_text = NIGHT.decode() + "01012,01012,104,W2,2026-11-16T23:45:00+08:00,unknown,1\n"
    second = client.post("/api/admission", json={"text": late_text, "params": SHIFT_PARAMS}).json
    assert second["superseded_by_late"] is True
    trigger = second["late_isolation_triggers"][0]
    assert trigger["bed"] == "104" and trigger["quarantined"] is True and trigger["code"] == "STATE"
    assert any(x["id"] == first["admission_id"] and x["status"] == "已失效"
               for x in second["superseded"])


def test_admission_history_keeps_both_versions(client):
    first = client.post("/api/admission", json={"text": NIGHT.decode(), "params": SHIFT_PARAMS}).json
    late_text = NIGHT.decode() + "01011,01010,004,W1,2026-11-16T23:30:00+08:00,admit,1\n"
    second = client.post("/api/admission", json={"text": late_text, "params": SHIFT_PARAMS}).json
    from app import store
    history = store.admission_history(second["shift_key"])
    assert [h["admission_id"] for h in history] == [first["admission_id"], second["admission_id"]]
    assert history[0]["status"] == "已失效" and history[1]["status"] == "现行"
    # 旧结论的逐床依据完整保留
    assert any(b["bed"] == "004" for b in history[0]["bed_board"])


def test_default_export_persists_conclusion(client):
    response = client.get("/api/admission.csv")
    assert response.status_code == 200
    assert client.get("/api/history").status_code == 200
