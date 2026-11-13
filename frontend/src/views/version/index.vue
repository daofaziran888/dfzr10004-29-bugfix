<template>
  <section class="page" data-module="version">
    <header class="page-head">
      <div>
        <h2>采集配置版本管理</h2>
        <p class="page-desc">维护采集配置的版本历史、变更记录与回滚控制，为采集配置管理提供可审计的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记配置版本</button>
        <button class="btn" type="button" @click="exportRows">导出采集配置版本清单</button>
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
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ display(row, column) }}</td>
          <td>
            <span class="status-tag" :data-status="String(row.status ?? '')">{{ row.status ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              :class="{ 'link-disabled': !isAllowed(row, action) }"
              type="button"
              :disabled="pendingAction !== ''"
              :title="isAllowed(row, action) ? '' : hintFor(row, action)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openDetail(row)">查看详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无采集配置版本数据，可先登记配置版本</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集配置版本记录</span>
      <span>状态口径：草稿 →（发布版本）已发布 →（回滚版本，仅一次）已回滚 →（废弃版本）已废弃</span>
    </footer>

    <div v-if="detailId !== null" class="detail-panel" data-role="version-detail">
      <header class="detail-head">
        <h3>配置版本详情</h3>
        <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
      </header>
      <p v-if="detailError" class="error-text" role="alert">{{ detailError }}</p>
      <dl v-else-if="detail" class="detail-grid">
        <div v-for="column in columns" :key="column" class="detail-item">
          <dt>{{ column }}</dt>
          <dd>{{ display(detail, column) }}</dd>
        </div>
        <div class="detail-item">
          <dt>当前状态</dt>
          <dd>{{ detail.status }}</dd>
        </div>
        <div class="detail-item">
          <dt>关联审计事件</dt>
          <dd>{{ detailEventCount }} 条</dd>
        </div>
      </dl>
      <p v-if="detail && detail.status === '已废弃'" class="detail-note">
        已废弃版本不能重新发布；如需恢复该配置，可基于它重建一个新版本（新版本号、从草稿开始）。
        <button class="btn" type="button" :disabled="pendingAction !== ''" @click="rebuild(detail)">重建新版本</button>
      </p>
    </div>

    <div v-if="createOpen" class="modal-mask" @click.self="closeCreate">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记配置版本</h3>
        <label v-for="field in requiredFields" :key="field" class="modal-field">
          <span>{{ field }}</span>
          <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
        </label>
        <label class="modal-field">
          <span>变更说明</span>
          <input v-model="createForm['变更说明']" placeholder="可选" />
        </label>
        <label class="modal-field">
          <span>变更内容</span>
          <input v-model="createForm['变更内容']" placeholder="可选" />
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

const ENDPOINT = '/api/version'
const columns = ["版本编号", "配置名称", "版本号", "变更说明", "变更内容", "回滚标识", "发布状态", "发布时间"]
const actions = ["发布版本", "回滚版本", "废弃版本"]
const statuses = ["草稿", "已发布", "已回滚", "已废弃"]
const requiredFields = ["版本编号", "配置名称", "版本号"]
const ALLOWED: Record<string, string[]> = {
  发布版本: ['草稿'],
  回滚版本: ['已发布'],
  废弃版本: ['已回滚'],
}
const HINTS: Record<string, string> = {
  发布版本: '仅草稿状态可发布；已回滚或已废弃版本不能重新发布',
  回滚版本: '仅已发布版本可回滚，且只能回滚一次；未发布稿不能回滚',
  废弃版本: '仅已回滚版本可标记废弃；未发布或已发布版本不能直接废弃',
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = ["版本编号", "配置名称"]
const pendingAction = ref('')
const detailId = ref<number | null>(null)
const detail = ref<Row | null>(null)
const detailError = ref('')
const detailEventCount = ref(0)
const createOpen = ref(false)
const createPending = ref(false)
const createError = ref('')
const createForm = ref<Record<string, string>>({})
const summary = ref<{ total: number; counts: Record<string, number> }>({ total: 0, counts: {} })

const stats = computed(() => [
  { label: '配置版本总数', value: summary.value.total },
  { label: '已发布版本', value: summary.value.counts['已发布'] ?? 0 },
  { label: '已回滚版本', value: summary.value.counts['已回滚'] ?? 0 },
  { label: '已废弃版本', value: summary.value.counts['已废弃'] ?? 0 },
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
  if (action === '发布版本' && status === '已废弃') return '已废弃版本不能重新发布；如需恢复请重建新版本'
  if (action === '发布版本' && status === '已回滚') return '已回滚版本仅可查看，不能重新发布'
  if (action === '回滚版本' && status === '已回滚') return '该版本已回滚，回滚动作只能执行一次'
  if (action === '废弃版本' && status === '已发布') return '请先回滚版本，回滚完成后才能标记废弃'
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
    successMessage.value = payload.message ?? '配置版本已登记'
    createOpen.value = false
    await reload()
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '配置版本登记失败'
  } finally {
    createPending.value = false
  }
}

async function openDetail(row: Row) {
  detailId.value = Number(row.id)
  detail.value = null
  detailError.value = ''
  try {
    const [detailResponse, auditResponse] = await Promise.all([
      request(`${ENDPOINT}/${row.id}`),
      request(`/api/audit?keyword=${encodeURIComponent(String(row['版本编号'] ?? ''))}&size=200`),
    ])
    if (!detailResponse.ok) {
      const payload = await detailResponse.json().catch(() => ({}))
      throw new Error(payload.detail || `配置版本详情读取失败（HTTP ${detailResponse.status}）`)
    }
    detail.value = await detailResponse.json()
    const auditPayload = auditResponse.ok ? await auditResponse.json() : { total: 0 }
    detailEventCount.value = Number(auditPayload.total ?? 0)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '配置版本详情读取失败'
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
  pendingAction.value = `${row.id}:${action}`
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
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集配置版本操作失败'
  } finally {
    pendingAction.value = ''
  }
}

async function rebuild(row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  pendingAction.value = `${row.id}:重建新版本`
  try {
    const response = await request(`${ENDPOINT}/${row.id}/rebuild`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || `重建失败（HTTP ${response.status}）`)
    }
    successMessage.value = payload.message ?? '已重建新版本'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重建新版本失败'
  } finally {
    pendingAction.value = ''
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
      throw new Error(payload.detail || `配置版本列表读取失败（HTTP ${response.status}）`)
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadSummary()
    if (detailId.value !== null) {
      const current = rows.value.find((row) => Number(row.id) === detailId.value)
      if (current) void openDetail(current)
      else closeDetail()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集配置版本列表读取失败'
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
.status-tag { font-weight: 600; }
.status-tag[data-status='已废弃'] { color: #b42318; }
.status-tag[data-status='已回滚'] { color: #b54708; }
.status-tag[data-status='已发布'] { color: #067647; }
.link:disabled { color: #98a2b3; cursor: not-allowed; }
.link-disabled { color: #98a2b3; }
.detail-panel { margin-top: 16px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.detail-head { display: flex; justify-content: space-between; align-items: center; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px 16px; margin: 8px 0 0; }
.detail-item dt { color: var(--muted); font-size: 12px; }
.detail-item dd { margin: 2px 0 0; font-size: 13px; }
.detail-note { font-size: 13px; color: var(--muted); display: flex; align-items: center; gap: 8px; }
.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; }
.modal { background: #fff; border-radius: 10px; padding: 16px 20px; width: 360px; }
.modal-field { display: block; margin: 8px 0; }
.modal-field span { display: block; font-size: 12px; color: var(--muted); }
.modal-field input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
