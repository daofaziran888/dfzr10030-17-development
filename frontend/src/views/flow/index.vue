<template>
  <section class="page" data-module="flow">
    <header class="page-head">
      <div><h2>数据流转编排</h2><p class="page-desc">维护流转管道，围绕管道编号、管道名称、起始节点、终止节点做登记、筛选与状态流转。</p></div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记流转管道</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出关系清单</button>
      </div>
    </header>
    <div class="dag-wrap" ref="dagRef">
      <svg class="dag-svg" :viewBox="`0 0 ${svgW} ${svgH}`">
        <defs><marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="#94a3b8"/></marker></defs>
        <g v-for="edge in graph.edges" :key="edge.from+edge.to">
          <line class="dag-edge" :x1="nodePos[edge.from]?.x||0" :y1="nodePos[edge.from]?.y||0" :x2="nodePos[edge.to]?.x||0" :y2="nodePos[edge.to]?.y||0"/>
          <text :x="(nodePos[edge.from]?.x+nodePos[edge.to]?.x)/2" :y="(nodePos[edge.from]?.y+nodePos[edge.to]?.y)/2 - 6" font-size="10" fill="#64748b" text-anchor="middle">{{ edge.label }}</text>
        </g>
        <g v-for="node in graph.nodes" :key="node.id" :class="['dag-node', {selected: selectedNode?.id===node.id}]" @click="selectNode(node)" @mousedown="startDrag($event, node)">
          <rect :x="nodePos[node.id]?.x - 60" :y="nodePos[node.id]?.y - 20" width="120" height="40" rx="6" :fill="nodeColor(node.status)"/>
          <text :x="nodePos[node.id]?.x" :y="nodePos[node.id]?.y + 4" text-anchor="middle">{{ node.name || node.label }}</text>
        </g>
      </svg>
    </div>
    <section class="field-preview" data-testid="flow-field-view">
      <header class="field-preview-head">
        <div>
          <h3>字段级血缘视图（同一快照）</h3>
          <p class="page-desc">编排服务读取与血缘页相同的字段路径快照，按上下游关系给出下一节点顺序。</p>
        </div>
        <div class="page-actions">
          <RouterLink class="btn" to="/lineage/field-view" data-testid="open-field-view">查看字段路径明细</RouterLink>
          <button class="btn" type="button" data-testid="sync-ledger" :disabled="syncing" @click="syncLedger">
            {{ syncing ? '同步中…' : '落到待复核台账' }}
          </button>
          <button class="btn" type="button" data-testid="refresh-flow-view" @click="reloadFieldView">刷新</button>
        </div>
      </header>
      <div class="stat-row" style="margin-bottom:0">
        <article v-for="item in orderSummary" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>
      <p class="page-desc" data-testid="flow-snapshot">
        快照 {{ flowSnapshot || '—' }} · {{ precedenceLabel }} · 来源 {{ fieldOrder.generated_from || '字段级血缘视图' }}
      </p>
      <ol class="node-order" data-testid="node-order">
        <li v-for="item in fieldOrder.order || []" :key="item.node">
          <strong>{{ item.position }}. {{ item.node }}</strong>
          <span v-if="item['下一节点'] && item['下一节点'].length"> → {{ item['下一节点'].join('、') }}</span>
        </li>
      </ol>
      <p v-if="flowMessage" class="page-desc" data-testid="flow-message">{{ flowMessage }}</p>

      <h4 style="margin:12px 0 6px">待复核台账</h4>
      <table class="data-table" v-if="ledgerItems.length">
        <thead><tr><th>台账</th><th>字段路径</th><th>关系版本</th><th>差异摘要</th><th>复核状态</th><th>核对结果</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="entry in ledgerItems" :key="entry.id" :data-testid="`ledger-${entry.id}`" :data-review-status="entry['复核状态']">
            <td>{{ entry.id }}</td>
            <td>{{ entry['字段路径'] }}</td>
            <td>{{ entry['关系版本'] }}</td>
            <td>{{ entry['差异摘要'] }}</td>
            <td>{{ entry['复核状态'] }}</td>
            <td>{{ entry['核对结果'] || '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" data-testid="review-agree" :disabled="entry['复核状态'] !== '待复核'" @click="review(entry, '一致')">核对一致</button>
              <button class="link" type="button" data-testid="review-diff" :disabled="entry['复核状态'] !== '待复核'" @click="review(entry, '存在差异')">标记差异</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-state">尚未同步：点击「落到待复核台账」读取当前快照。</p>
    </section>
    <div v-if="selectedNode" class="dag-detail" data-testid="dag-detail">
      <dl><dt>编号</dt><dd>{{ selectedNode.id }}</dd></dl>
      <dl><dt>名称</dt><dd>{{ selectedNode.name || selectedNode.label }}</dd></dl>
      <dl v-for="f in detailFields" :key="f"><dt>{{ f }}</dt><dd>{{ selectedNode[f] ?? '—' }}</dd></dl>
      <div style="grid-column:1/-1; margin-top:8px; display:flex; gap:6px;">
        <button v-for="action in actions" :key="action" class="btn" :data-testid="`action-${selectedNode.id}-${action}`" @click="runAction(action, selectedNode)">{{ action }}</button>
      </div>
    </div>
    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate"><h3>登记流转管道</h3>
        <label v-for="field in columns.slice(0,3)" :key="field" class="create-field"><span>{{ field }}</span><input v-model="createValues[field]" :placeholder="`请输入${field}`" required /></label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-create">提交</button>
        </div>
      </form>
    </div>
    <footer class="page-foot"><span>{{ graph.nodes.length }} 个节点</span></footer>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { fetchJson, request } from '@/api/client'
const ENDPOINT = '/api/flow'
const FIELD_ENDPOINT = '/api/flow/field-view'
const columns = ["管道编号", "管道名称", "起始节点", "终止节点", "中间节点", "流转条件", "并行数量", "管道状态"]
const actions = ["提交校验", "启用管道", "停用管道"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const STATUS_COLORS: Record<string,string> = { '草稿':'#e2e8f0','待校验':'#fef3c7','已启用':'#d1fae5','已停用':'#fee2e2' }
const nodeColor = (s: string) => STATUS_COLORS[s] || '#e2e8f0'
const graph = reactive<{nodes: any[], edges: any[]}>({ nodes: [], edges: [] })
const nodePos = ref<Record<number,{x:number;y:number}>>({})
const selectedNode = ref<any>(null)
const detailFields = computed(() => columns.filter((f: string) => !['id','name','label','status'].includes(f)))
const svgW = ref(800); const svgH = ref(400)
const errorMessage = ref('')
const creating = ref(false); const showCreate = ref(false)
const createValues = ref<Record<string,string>>({})
let dragNode: any = null; const dagRef = ref<HTMLElement|null>(null)
function layout(nodes: any[]) {
  const w = 800; const h = Math.max(400, nodes.length * 80)
  svgW.value = w; svgH.value = h
  const pos: Record<number,{x:number;y:number}> = {}
  nodes.forEach((n,i) => { pos[n.id] = { x: 120 + (i % 5) * 150, y: 50 + Math.floor(i / 5) * 80 } })
  nodePos.value = pos
}
function selectNode(node: any) { selectedNode.value = selectedNode.value?.id === node.id ? null : node }
function startDrag(e: MouseEvent, node: any) { dragNode = node; const onMove = (ev: MouseEvent) => { if(!dragNode) return; nodePos.value[node.id] = { x: ev.offsetX, y: ev.offsetY } }; const onUp = () => { dragNode = null; document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp) }; document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp) }
async function reload() {
  try { const res = await request(ENDPOINT); const data = await res.json(); graph.nodes = data.nodes || []; graph.edges = data.edges || []; layout(graph.nodes) } catch(e) { errorMessage.value = '加载失败' }
}
async function runAction(action: string, node: any) {
  try { await request(`${ENDPOINT}/${node.id}/actions`, { method: 'POST', body: JSON.stringify({ action }) }); await reload() } catch(e) { errorMessage.value = '操作失败' }
}
function openCreate() { createValues.value = {}; showCreate.value = true }
async function submitCreate() {
  try { const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: createValues.value }) }); if(!res.ok) throw new Error('创建失败'); showCreate.value = false; await reload() } catch(e: any) { errorMessage.value = e.message }
}
function exportRows() { window.open(`${ENDPOINT}/export`, '_blank') }

const fieldOrder = ref<any>({ order: [], edges: [] })
const flowSnapshot = ref('')
const precedence = ref('verified')
const precedenceLabel = ref('验证优先')
const syncAttempted = ref(false)
const ledgerItems = ref<any[]>([])
const flowMessage = ref('')
const syncing = ref(false)
const orderSummary = computed(() => [
  { label: '节点顺序数', value: (fieldOrder.value.order || []).length },
  { label: '字段路径数', value: (fieldOrder.value.paths || []).length },
  { label: '冲突字段数', value: fieldOrder.value.conflict_count ?? 0 },
  { label: '待复核台账', value: ledgerItems.value.filter((e: any) => e['复核状态'] === '待复核').length },
])
async function loadLedger() {
  try {
    const data = await fetchJson<any>(`${FIELD_ENDPOINT}/ledger`)
    ledgerItems.value = data.items || []
  } catch (e) { ledgerItems.value = [] }
}
async function reloadFieldView() {
  try {
    const data = await fetchJson<any>(`${FIELD_ENDPOINT}/order?precedence=${precedence.value}`)
    fieldOrder.value = data
    flowSnapshot.value = data.snapshot || ''
    precedenceLabel.value = data.precedence_label || ''
  } catch (e) { flowMessage.value = '字段级血缘视图读取失败' }
  await loadLedger()
}
async function syncLedger() {
  syncing.value = true
  try {
    const res = await request(`${FIELD_ENDPOINT}/ledger/sync`, {
      method: 'POST',
      body: JSON.stringify({ values: { precedence: precedence.value, snapshot: flowSnapshot.value } }),
    })
    const data = await res.json()
    if (res.status === 409) {
      flowMessage.value = data.message || '同快照并发写仅一次成功：本次写入已拒绝，已有台账保留'
    } else {
      flowMessage.value = data.message || '已同步到待复核台账'
    }
    await loadLedger()
  } catch (e) { flowMessage.value = '同步待复核台账失败' }
  syncing.value = false
}
async function review(entry: any, result: string) {
  try {
    const res = await request(`${FIELD_ENDPOINT}/ledger/${entry.id}/review`, {
      method: 'POST',
      body: JSON.stringify({ values: { result, snapshot: entry['快照'] } }),
    })
    const data = await res.json()
    if (res.status === 409) {
      flowMessage.value = data.message || '同快照并发写仅一次成功：本次核对未写入，旧结果保留'
    } else {
      flowMessage.value = data.message || '核对结果已写入字段级血缘视图'
    }
    await loadLedger()
  } catch (e) { flowMessage.value = '复核写回失败' }
}
onMounted(() => { reload(); reloadFieldView() })
</script>
<style scoped>
.field-preview { margin-top: 12px; padding: 12px; background: #fff; border: 1px solid var(--border); border-radius: 8px; }
.field-preview-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.field-preview-head h3 { margin: 0; font-size: 15px; }
</style>
