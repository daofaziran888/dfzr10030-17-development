<template>
  <section class="page" data-module="lineage">
    <header class="page-head">
      <div><h2>数据血缘追踪</h2><p class="page-desc">维护血缘关系，围绕关系编号、上游节点、下游节点、来源字段做登记、筛选与状态流转。</p></div>
      <div class="page-actions">
        <RouterLink class="btn" to="/lineage/field-view" data-testid="open-field-view">字段级血缘视图</RouterLink>
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记血缘关系</button>
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
    <section class="field-preview" data-testid="field-view-preview">
      <header class="field-preview-head">
        <div>
          <h3>字段级血缘概览</h3>
          <p class="page-desc">这里只列出汇总；展开字段路径、切换关系版本和查看差异请进入独立视图。</p>
        </div>
        <RouterLink class="btn" :to="`/lineage/field-view?precedence=${precedence}`" data-testid="field-view-detail-entry">
          查看字段路径明细
        </RouterLink>
      </header>
      <div class="stat-row" style="margin-bottom:0">
        <article v-for="metric in fieldMetrics" :key="metric.key" class="stat-card">
          <span class="stat-label">{{ metric.label }}</span>
          <strong class="stat-value">{{ metric.value }}</strong>
        </article>
      </div>
      <p class="page-desc">快照 {{ fieldSnapshot || '—' }} · 业务边界 {{ fieldPrecedenceLabel }}</p>
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
      <form class="modal" @submit.prevent="submitCreate"><h3>登记血缘关系</h3>
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
const ENDPOINT = '/api/lineage'
const columns = ["关系编号", "上游节点", "下游节点", "来源字段", "目标字段", "转换说明", "采集批次", "关系版本"]
const actions = ["记录关系", "验证关系", "归档关系"]
const statuses = ["草稿", "已记录", "已验证", "已归档"]
const fieldMetrics = ref<{key:string;label:string;value:number}[]>([])
const fieldSnapshot = ref('')
const fieldPrecedenceLabel = ref('')
const precedence = ref('verified')
async function loadFieldSummary() {
  try {
    const data = await fetchJson<any>(`${ENDPOINT}/field-view/summary?precedence=${precedence.value}`)
    fieldMetrics.value = data.metrics || []
    fieldSnapshot.value = data.snapshot || ''
    fieldPrecedenceLabel.value = data.precedence_label || ''
  } catch (e) { fieldMetrics.value = [] }
}
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
onMounted(() => { reload(); loadFieldSummary() })
</script>
<style scoped>
.field-preview { margin-top: 12px; padding: 12px; background: #fff; border: 1px solid var(--border); border-radius: 8px; }
.field-preview-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.field-preview-head h3 { margin: 0; font-size: 15px; }
</style>
