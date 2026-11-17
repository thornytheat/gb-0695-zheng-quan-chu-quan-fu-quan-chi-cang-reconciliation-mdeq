# 接口

`POST /api/run`：JSON中的csv文本，省略使用样例；或multipart字段file。最大5MB。

`POST /api/m1`至`POST /api/m7`：独立运行到指定模块，JSON结构同运行接口。带`base`（原批次id）时按修订派生语义运行，返回该阶段结果并附带`run_id`、`parent`、`status`、`progress`；若该阶段尚未确认且不能被本次请求确认，返回409。

`GET /api/history`：历史批次（含`parent`、`status`、`progress`）。`POST /api/review`接收id、revision和decision（通过或退回）。`GET /api/evidence.csv`提供单一证据导出。

## 修订派生

`POST /api/derive`：请求体`base`（原批次id，必填）、`csv`（修订CSV，省略时使用原批次留存原文）、`stop`（续跑至第几阶段，默认7）；也接受multipart字段`file`。等价入口`POST /api/runs/<id>/derive`与`GET /api/runs/<id>/derivations`。

派生批次是独立批次：`parent`指向原批次，因子（M4/M7）取最新有效版本，权益登记、持仓与现金对账（M3/M5/M6）保留原批次登记时版本。原批次的payload、复核状态与证据不被改写。

- 派生完成（`status=complete`）后，同一`base`+同一CSV再次提交复用原派生批次（`reused=true`），不重算、不覆盖其复核状态。
- 中断（`stop<7`，`status=partial`）时未确认阶段不发布；再次提交从最后确认阶段续跑，`manifest.resumed_from_stage`记录起点，页面与历史始终可查原批次。
- 晚到晚于当前版本总是被忽略并列入`晚到记录`，当前版本不回退；同版本不同定义隔离为`CONFLICT`，已复核定义保留。
- 派生中与不完整的批次不能复核（409 `INCOMPLETE`），不能作为其他派生的原批次。

结构错误422，版本变化409，原批次不存在404，文件过大413；错误返回code与message。所有页面可从左侧七个模块入口访问。

`GET /api/runs/<id>`读取指定批次；页面与证据CSV通过run_id查询参数绑定批次，模块导航保留该参数。派生批次的证据CSV额外包含`派生关联`、`派生权益`与`晚到记录`行。

JSON请求体必须是对象，数组、标量、null、损坏JSON及错误上传字段返回400，且不运行默认样例。
