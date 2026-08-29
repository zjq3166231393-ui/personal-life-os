# Personal Life OS — 设计规范

> 技术栈：Bootstrap 5.3 + Noto Sans SC + 原生 CSS

---

## 一、颜色系统

### 主色

| Token | Hex | 用途 |
|-------|-----|------|
| `--primary` | `#2563eb` | 导航、链接、主按钮 |
| `--primary-dark` | `#1e40af` | Hero 渐变起始色 |
| `--primary-gradient` | `linear-gradient(135deg, #1e40af, #2563eb)` | Hero 区域背景 |

### 功能色

| Token | Hex | 用途 |
|-------|-----|------|
| `--success` | `#22c55e` | 收入、完成、预算充足 |
| `--danger` | `#ef4444` | 支出、超支、错误、删除 |
| `--warning` | `#f59e0b` | 预警、进行中状态 |
| `--info` | `#06b6d4` | 转账、信息提示 |

### 中性色

| Token | Hex | 用途 |
|-------|-----|------|
| `--bg` | `#f5f7fb` | 页面背景 |
| `--surface` | `#ffffff` | 卡片背景 |
| `--text-primary` | `#1f2937` | 正文 |
| `--text-secondary` | `#6b7280` | 辅助文字 |

### 状态标签

| 状态 | Badge class | 示例 |
|------|------------|------|
| 待办 / active | `text-bg-primary` | 蓝色 |
| 已完成 / 成功 | `text-bg-success` | 绿色 |
| 已取消 / 忽略 | `text-bg-secondary` | 灰色 |
| 逾期 / 超支 | `text-bg-danger` | 红色 |
| 进行中 | `text-bg-warning` | 黄色 |

---

## 二、字体

| 属性 | 值 |
|------|-----|
| 主字体 | `'Noto Sans SC', system-ui, sans-serif` |
| 标题 | `font-weight: 600`（h5: 1.15rem, h4: 1.35rem） |
| 正文 | `font-size: 0.95rem` |
| 辅助文字 | `font-size: 0.75rem`, `color: var(--text-secondary)` |
| 等宽（金额） | 继承主字体，`font-weight: 700` |

---

## 三、间距

| Token | 值 | 用途 |
|-------|-----|------|
| `py-3 py-md-4` | 页面垂直内边距 | 所有页面容器 |
| `p-3 p-md-4` | 卡片内边距 | 所有 card 内部 |
| `g-2` | 栅格间距 | 行内列间距 |
| `mb-3` | 卡片间距 | 各 section 之间 |
| `mt-2` | 组件间距 | 按钮组、详情区 |

---

## 四、卡片

```css
.card { border: 0; border-radius: 18px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
```

| 变体 | 用法 |
|------|------|
| `.card.shadow-sm` | 标准卡片 |
| `.hero` | Hero 区域：渐变背景 + 白色文字 + `border-radius: 22px` |
| `.hero-danger` | 危险操作 Hero：`#dc2626 → #991b1b` |

---

## 五、按钮

| 类型 | class | 用途 |
|------|-------|------|
| 主按钮 | `btn btn-dark` 或 `btn btn-primary` | 确认、保存、提交 |
| 次按钮 | `btn btn-outline-secondary btn-sm` | 编辑、取消 |
| 危险按钮 | `btn btn-danger` 或 `btn btn-outline-danger btn-sm` | 删除、停用 |
| 成功按钮 | `btn btn-success` 或 `btn btn-outline-success btn-sm` | 完成、还款 |
| 语音按钮 | `.mic-btn` — 52×52px 圆形 | 首页语音输入 |

---

## 六、表单

```css
.form-control { border-radius: 10px; }
.form-label { font-weight: 500; font-size: 0.875rem; margin-bottom: 0.25rem; }
```

| 组件 | class |
|------|-------|
| 文本输入 | `form-control form-control-sm` |
| 下拉框 | `form-select form-select-sm` |
| 开关 | `form-check form-switch` |
| 颜色选择器 | `form-control form-control-color` |

---

## 七、页头（Hero）

所有页面使用统一 Hero 样式：

```html
<section class="hero p-3 p-md-4 shadow-sm mb-3 d-flex justify-content-between">
  <div><h1 class="h5 fw-bold mb-0">页面标题</h1></div>
  <a href="{% url 'home' %}" class="text-white text-decoration-none small">← 首页</a>
</section>
```

---

## 八、模板文件清单

### 需要修改以统一样式

| 页面 | 模板 | 优先级 |
|------|------|--------|
| 登录 | `accounts/login.html` | P1 |
| 注册 | `accounts/register.html` | P1 |
| 今日页 | `life/home.html` | P1（已接近规范） |
| 财务看板 | `life/dashboard.html` | P1（已接近规范） |
| 任务列表 | `life/task_list.html` | P2 |
| 账目列表 | `life/expense_list.html` | P2 |
| 预算 | `life/budget.html` | P2 |
| 提醒列表 | `life/reminder_list.html` | P2 |
| 复盘 | `life/review.html` | P2 |
| 个人设置 | `accounts/profile.html` | P2 |
| AI 确认卡 | `home.html` 内 JS | P2 |

### 无需修改（功能简单、样式可接受）

其他 29 个模板（detail/edit/delete/create 类）保持现状。
