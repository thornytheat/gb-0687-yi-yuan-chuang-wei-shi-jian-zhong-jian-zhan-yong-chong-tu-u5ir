# 运行维护

主服务端口与Compose保持一致。数据保存在records命名卷，应用代码不需要写入权限。

```sh
docker compose logs --tail=80 app
docker compose restart app
docker compose exec -T app pytest -q
docker compose down --remove-orphans
```

健康检查GET /health。失败输入返回原因码，结构性错误422，过大文件413，陈旧复核409。
文件内容最大4MB。批次先计算再原子保存，失败不留半条记录。证据CSV按原始行号定位。
收治结论经 POST /api/admission 落库（admissions 表），同一输入哈希重试不新增行；GET /api/admission.csv?admission=<结论ID> 导出与页面同源的结论，无参数时按默认样例即时出具并落库。晚到隔离记录触发的失效链通过同班次 shift_key 串联，旧结论 payload 不删除。
