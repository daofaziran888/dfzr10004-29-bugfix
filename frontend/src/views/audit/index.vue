<template>
  <section class="page" data-module="audit">
    <header class="page-head">
      <div>
        <h2>采集审计日志管理</h2>
        <p class="page-desc">维护采集审计的操作类型、执行记录与复核状态，为采集合规审计提供可追溯的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记审计记录</button>
        <button class="btn" type="button" @click="exportRows">导出采集审计日志清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="errorMessage" class="banner error-text" role="alert">{{ errorMessage }}</p>
    <p v-if="successMessage" class="banner success-text" role="status">{{ successMessage }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>版本编号</th>
          <th>版本号</th>
          <th>发布结论</th>
          <th>版本当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '记录编号'" class="link" type="button" @click="openDetail(row)">
              {{ display(row, column) }}
            </button>
            <template v-else>{{ display(row, column) }}</template>
          </td>
          <td>{{ display(row, '版本编号') }}</td>
          <td>{{ display(row, '版本号') }}</td>
          <td>{{ display(row, '发布结论') }}</td>
          <td>{{ display(row, '版本当前状态') }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              :class="{ 'link-disabled': !isAllowed(row, action) }"
              type="button"
              :disabled="actingId === Number(row.id)"
              :title="isAllowed(row, action) ? '' : hintFor(row, action)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 5" class="empty-state">暂无采集审计日志数据，可先登记审计记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集审计日志记录</span>
      <span>配置版本动作生成的审计事件从「待核对」开始，处置后依次进入已复核、已归档</span>
    </footer>

    <div v-if="detailId !== null" class="detail-panel" data-role="audit-detail">
      <header class="detail-head">
        <h3>审计记录详情</h3>
        <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
      </header>
      <p v-if="detailError" class="error-text" role="alert">{{ detailError }}</p>
      <dl v-else-if="detail" class="detail-grid">
        <div v-for="field in detailFields" :key="field" class="detail-item">
          <dt>{{ field }}</dt>
          <dd>{{ display(detail, field) }}</dd>
        </div>
        <div class="detail-item">
          <dt>当前状态</dt>
          <dd>{{ display(detail, 'status') }}</dd>
        </div>
        <div class="detail-item">
          <dt>关联配置版本</dt>
          <dd>{{ display(detail, '版本编号') }} / {{ display(detail, '版本号') }}</dd>
        </div>
        <div class="detail-item">
          <dt>发布结论</dt>
          <dd>{{ display(detail, '发布结论') }}</dd>
        </div>
        <div class="detail-item">
          <dt>版本当前状态</dt>
          <dd>{{ display(detail, '版本当前状态') }}</dd>
        </div>
        <div class="detail-item">
          <dt>动作前状态</dt>
          <dd>{{ display(detail, '版本前状态') }}</dd>
        </div>
      </dl>
      <p v-if="detail && detail.来源模块 === '采集配置版本'" class="detail-note">
        该事件由配置版本动作成功落库后生成，结论与配置版本列表同源；二者冲突时以成功落库的版本记录为准。
      </p>
    </div>

    <div v-if="createOpen" class="modal-mask" @click.self="closeCreate">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记审计记录</h3>
        <label v-for="field in requiredFields" :key="field" class="modal-field">
          <span>{{ field }}</span>
          <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
        </label>
        <label v-for="field in ['执行人', '结果摘要', '变更内容']" :key="field" class="modal-field">
          <span>{{ field }}</span>
          <input v-model="createForm[field]" placeholder="可选" />
        </label>
        <p v-if="createError" class="error-text" role="alert">{{ createError }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeCreate">取消</button>
          <button class="btn primary" type="submit" :disabled="createPending">
            {{ createPending ? '提交中…' : '登记' }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null | undefined>

const ENDPOINT = '/api/audit'
const columns = ["记录编号", "操作类型", "采集计划", "执行人", "执行时间", "结果摘要", "变更内容", "复核状态"]
const actions = ["标记复核", "归档记录"]
const statuses = ["已记录", "待核对", "已复核", "已归档"]
const requiredFields = ["记录编号", "操作类型", "采集计划"]
const ALLOWED: Record<string, string[]> = {
  标记复核: ['已记录', '待核对'],
  归档记录: ['已复核'],
}
const HINTS: Record<string, string> = {
  标记复核: '仅已记录或待核对的审计记录可以标记复核',
  归档记录: '仅已复核的审计记录可以归档',
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = ["记录编号", "采集计划"]
const actingId = ref<number | null>(null)
const detailId = ref<number | null>(null)
const detail = ref<Row | null>(null)
const detailError = ref('')
const createOpen = ref(false)
const createPending = ref(false)
const createError = ref('')
const createForm = ref<Record<string, string>>({})
const summary = ref<{ total: number; counts: Record<string, number> }>({ total: 0, counts: {} })
const detailFields = ["记录编号", "操作类型", "采集计划", "执行人", "执行时间", "结果摘要", "变更内容", "复核状态"]

const stats = computed(() => [
  { label: '审计记录总数', value: summary.value.total },
  { label: '待核对记录', value: summary.value.counts['待核对'] ?? 0 },
  { label: '已复核记录', value: summary.value.counts['已复核'] ?? 0 },
  { label: '已归档记录', value: summary.value.counts['已归档'] ?? 0 },
])

function display(row: Row | null, column: string): string {
  const value = row?.[column]
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function isAllowed(row: Row, action: string): boolean {
  return (ALLOWED[action] ?? []).includes(String(row.status ?? ''))
}

function hintFor(row: Row, action: string): string {
  const status = String(row.status ?? '')
  if (action === '标记复核' && status === '已复核') return '该记录已复核，不能重复标记复核'
  if (action === '标记复核' && status === '已归档') return '已归档记录不能标记复核'
  if (action === '归档记录' && status === '已归档') return '该记录已归档，不能重复归档'
  if (action === '归档记录' && status !== '已复核') return '记录尚未复核，不能直接归档；请先标记复核'
  return HINTS[action] ?? ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createForm.value = {}
  createError.value = ''
  createOpen.value = true
}

function closeCreate() {
  createOpen.value = false
}

async function submitCreate() {
  createError.value = ''
  createPending.value = true
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || `登记失败（HTTP ${response.status}）`)
    }
    successMessage.value = payload.message ?? '审计记录已登记'
    createOpen.value = false
    await reload()
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '审计记录登记失败'
  } finally {
    createPending.value = false
  }
}

async function openDetail(row: Row) {
  detailId.value = Number(row.id)
  detail.value = null
  detailError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail || `审计记录详情读取失败（HTTP ${response.status}）`)
    }
    detail.value = await response.json()
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '审计记录详情读取失败'
  }
}

function closeDetail() {
  detailId.value = null
  detail.value = null
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  if (!isAllowed(row, action)) {
    errorMessage.value = hintFor(row, action)
    return
  }
  actingId.value = Number(row.id)
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || `动作未生效（HTTP ${response.status}）`)
    }
    successMessage.value = payload.message ?? '动作已生效'
    await reload()
    if (detailId.value === Number(row.id)) {
      await openDetail(row)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集审计日志操作失败'
  } finally {
    actingId.value = null
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) query.set(key, value)
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail || `审计记录列表读取失败（HTTP ${response.status}）`)
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadSummary()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集审计日志列表读取失败'
  }
}

async function loadSummary() {
  try {
    const response = await request(`${ENDPOINT}/summary`)
    if (!response.ok) return
    const payload = await response.json()
    summary.value = { total: Number(payload.total ?? 0), counts: payload.counts ?? {} }
  } catch {
    // 汇总卡片失败不影响列表本身，保留上一次的统计值。
  }
}

onMounted(reload)
</script>

<style scoped>
.banner { margin: 0 0 8px; font-size: 13px; }
.success-text { color: #067647; }
.link:disabled { color: #98a2b3; cursor: not-allowed; }
.link-disabled { color: #98a2b3; }
.detail-panel { margin-top: 16px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.detail-head { display: flex; justify-content: space-between; align-items: center; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px 16px; margin: 8px 0 0; }
.detail-item dt { color: var(--muted); font-size: 12px; }
.detail-item dd { margin: 2px 0 0; font-size: 13px; }
.detail-note { font-size: 12px; color: var(--muted); }
.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; }
.modal { background: #fff; border-radius: 10px; padding: 16px 20px; width: 360px; }
.modal-field { display: block; margin: 8px 0; }
.modal-field span { display: block; font-size: 12px; color: var(--muted); }
.modal-field input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
