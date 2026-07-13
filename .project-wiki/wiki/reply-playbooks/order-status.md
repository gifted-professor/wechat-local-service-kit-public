---
type: reply_policy
status: active
confidence: 0.66
privacy: public
sources:
  - ../architecture/reply-time-knowledge-layer.md
supersedes: []
last_verified: 2026-04-21
---

# Order Status Playbook

## Match Keywords

订单, 订单号, 下单, 购买, 买了, 付款, 支付, 发票, 尾款, 定金, 拍了

## Draft Guidance

- Identify whether the user wants order status, shipment status, or after-sales handling.
- If no order identifier is present, ask for the order number or screenshot.
- If the user mentions "last time" or "previously", say you will check the record first.

## Safety Rules

- Do not invent order status.
- Do not promise processing result before checking.
- Do not repeat private order or contact details from history.
- If the latest message is ambiguous, ask one short clarifying question.

## Clarifying Questions

- 你把订单号或截图发我一下，我帮你看。
- 我先帮你查一下这个订单现在到哪一步。
- 是想查发货进度，还是售后处理进度？
