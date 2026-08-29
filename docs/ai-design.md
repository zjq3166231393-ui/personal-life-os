# Personal Life OS — AI 解析设计

## 设计原则

1. **规则优先，AI 兜底** — 简单输入不调用 AI
2. **AI 只生成草稿** — 用户确认后才写数据库
3. **敏感内容不发送** — 身份证/密码/银行卡 → 本地解析
4. **成本可控** — 日上限、调用计数、用户开关

## 架构

```
用户输入 (语音/文字)
     │
     ▼
 route_parse()  ──── 路由器
     │
     ├─ confidence=high + single-intent → 规则解析 (本地)
     │
     ├─ AI 开关关闭 / 超日限 / 敏感内容 → Fallback
     │
     └─ 其他 → AI Provider
              │
              ├─ FakeProvider (无 Key / 测试)
              └─ DeepSeekProvider (生产)
                    │
                    ▼
              Schema 校验 (validate_ai_response)
                    │
                    ├─ 通过 → 确认卡
                    └─ 失败 → Fallback
```

## 三级路由

| 来源 | 触发条件 | 结果 |
|------|----------|------|
| `rule` | 高置信度 + 单一意图 | 直接返回 |
| `ai` | 低置信度或多意图 + AI 通过校验 | AI 解析结果 |
| `fallback` | AI 不可用/失败/校验失败 | 规则草稿 + error 信息 |

## Schema 设计

7 种 intent + 每 action 唯一 `action_id`：

| Intent | 必填字段 |
|--------|----------|
| `create_expense` | amount, category, occurred_at |
| `create_income` | amount, occurred_at |
| `create_task` | title |
| `create_reminder` | title, event_at 或 remind_at |
| `create_note` | title |
| `update_draft` | action_id |
| `unknown` | — |

## 数据模型

```
ConversationLog (原始输入)
  └── ParseResult (解析输出 + 置信度)
        └── ProposedAction (每条待确认操作)
```

- ConversationLog 记录 model / token_count / cost
- 不存储 API Key
- ProposedAction 永不自定写入 Expense/Task/Reminder

## Provider 接口

```python
class AIProvider(ABC):
    def parse(self, text: str) -> dict: ...
```

- `FakeProvider`：本地规则匹配（测试/开发）
- `DeepSeekProvider`：OpenAI SDK 兼容（生产）
- 切换：`set_provider(MyProvider())`

## 成本控制

| 机制 | 实现 |
|------|------|
| 用户开关 | `UserProfile.ai_parsing_enabled` |
| 日上限 | `UserProfile.daily_ai_limit` |
| 敏感检测 | `_check_sensitive()` — 4 种模式 |
| 成本记录 | `ConversationLog.token_count` / `cost` |

## 评测

```powershell
python manage.py run_eval
# FakeProvider: 21/24 passed (87.5%)
```

评测集：`tests/fixtures/parser_cases.json` — 25 个用例，12 种场景。
