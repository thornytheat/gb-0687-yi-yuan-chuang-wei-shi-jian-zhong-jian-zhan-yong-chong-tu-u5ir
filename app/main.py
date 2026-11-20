import csv,io,json
from flask import Flask,request,jsonify,redirect,Response
from importlib import import_module
from app.config import STAGES,MAX_BYTES
from app.errors import DomainError
from app.normalize import serial
from app.pipeline import run
from app import store
from app.web import view
def create_app():
    app=Flask(__name__);app.config["MAX_CONTENT_LENGTH"]=MAX_BYTES
    app.json.ensure_ascii=False
    @app.errorhandler(DomainError)
    def domain_error(error): return jsonify(code=error.code,message=error.message),error.status
    @app.errorhandler(413)
    def large(error): return jsonify(code="TOO_LARGE",message="文件超过4MB"),413
    @app.get("/health")
    def health(): return {"status":"ok"}
    @app.get("/")
    def home(): return redirect("/m/1")
    @app.get("/m/<int:stage>")
    def page(stage): return view(stage)
    @app.post("/api/runs")
    def start():
        if request.files:
            file=request.files.get("file")
            if file is None: raise DomainError("FILE","请选择文件")
            raw=file.read(MAX_BYTES+1)
            try: params=json.loads(request.form.get("params","{}"))
            except ValueError: raise DomainError("PARAMS","参数格式错误")
        else:
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict): raise DomainError("BODY","请求必须是对象")
            raw=payload.get("text")
            if raw is not None and not isinstance(raw,str): raise DomainError("TEXT","数据必须为文本")
            params=payload.get("params")
        return jsonify(run(raw,params))
    @app.get("/api/runs/<run_id>")
    def get_run(run_id): return jsonify(store.fetch(run_id))
    @app.get("/api/history")
    def history(): return jsonify(store.history())
    @app.post("/api/review/<run_id>")
    def review(run_id):
        data=request.get_json(silent=True)
        if not isinstance(data,dict): raise DomainError("BODY","请求必须是对象")
        return jsonify(store.review(run_id,data.get("version"),data.get("decision")))
    @app.get("/api/evidence.csv")
    def evidence():
        result=store.fetch(request.args["run"]) if request.args.get("run") else run()
        stream=io.StringIO(newline="");writer=csv.writer(stream)
        writer.writerow(["批次","行号","主键","原因码","证据"])
        for row in result.get("records",[]):
            writer.writerow([result["manifest"]["run_id"],row.get("_line",""),row.get("id",row.get("event_id","")),"PASS",json.dumps(serial(row),ensure_ascii=False)])
        for row in result["quarantine"]:
            writer.writerow([result["manifest"]["run_id"],row["line"],row["key"],row["code"],json.dumps(row,ensure_ascii=False)])
        return Response("\ufeff"+stream.getvalue(),content_type="text/csv; charset=utf-8",headers={"Content-Disposition":"attachment; filename=evidence.csv"})
    @app.get("/api/admission.csv")
    def admission_csv():
        # \u5bfc\u51fa\u76f4\u63a5\u8bfb\u53d6\u5df2\u4fdd\u5b58\u7684\u540c\u4e00\u6536\u6cbb\u7ed3\u8bba\uff0c\u9875\u9762\u4e0e\u5bfc\u51fa\u5171\u7528\u4e00\u4e2a payload
        payload=store.fetch_admission(request.args["admission"]) if request.args.get("admission") else store.save_admission(run(stop=8))
        stream=io.StringIO(newline="");writer=csv.writer(stream)
        c=payload["counts"]
        writer.writerow(["\u533a\u5757","\u952e","\u503c","\u539f\u56e0\u7801","\u8bf4\u660e"])
        writer.writerow(["\u7ed3\u8bba","admission_id",payload["admission_id"],"",payload["summary"]])
        writer.writerow(["\u7ed3\u8bba","status",payload["status"],payload.get("supersede_reason",""),payload.get("reserve_policy","")])
        for key,label in (("general_admissible","\u666e\u901a\u53ef\u6536\u6cbb\u5e8a"),("reserve_margin","\u7591\u4f3c\u4fdd\u7559\u9694\u79bb\u4f59\u91cf"),
                          ("isolation_free","\u7a7a\u95f2\u9694\u79bb\u5e8a"),("occupied_beds","\u5360\u7528\u5e8a"),
                          ("critical_admitted","\u672c\u6b21\u53ef\u6536\u91cd\u75c7"),("reserve_shortage","\u9694\u79bb\u4f59\u91cf\u7f3a\u53e3")):
            writer.writerow(["\u5408\u8ba1",label,c[key],"",""])
        for t in payload.get("late_isolation_triggers",[]):
            writer.writerow(["\u5931\u6548\u89e6\u53d1",t["id"],t["bed"],t["code"],"\u884c\u53f7 %s %s"%(t["line"],t["time"])])
        for old in payload.get("superseded",[]):
            writer.writerow(["\u65e7\u7ed3\u8bba",old["id"],old["status"],"SUPERSEDED","\u665a\u5230\u9694\u79bb\u8bb0\u5f55\u4f7f\u65e7\u7ed3\u8bba\u5931\u6548\uff0c\u539f\u5e8a\u4f4d\u53bb\u5411\u4e0e\u4f9d\u636e\u7559\u6863"])
        writer.writerow([])
        writer.writerow(["\u5e8a\u53f7","\u75c5\u533a","\u4e13\u7528\u9694\u79bb","\u5360\u7528","\u7591\u4f3c\u4fdd\u7559","\u53ef\u6536\u6cbb","\u666e\u901a\u6536\u6cbb","\u539f\u56e0\u7801","\u539f\u56e0","\u5e8a\u4f4d\u4f9d\u636e"])
        for bed in payload["bed_board"]:
            writer.writerow([bed["bed"],bed["ward"],"\u662f" if bed["isolation"] else "\u5426",
                             "\u662f" if bed["occupied"] else "\u5426","\u662f" if bed["reserved"] else "\u5426",
                             "\u662f" if bed["admissible"] else "\u5426",
                             "\u662f" if bed["general_admissible"] else "\u5426",
                             bed["code"],bed["reason"],
                             json.dumps(serial(bed["basis"]),ensure_ascii=False)])
        return Response("\ufeff"+stream.getvalue(),content_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":"attachment; filename=admission.csv"})
    for n,name in enumerate(STAGES,1): app.register_blueprint(import_module(f"app.modules.m{n}_{name}.handler").bp)
    return app
app=create_app()
