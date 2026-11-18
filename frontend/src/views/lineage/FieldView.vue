<template>
  <section class="page" data-module="lineage-field-view">
    <header class="page-head">
      <div>
        <h2>字段级血缘视图</h2>
        <p class="page-desc">
          按来源字段、目标字段和采集批次解析血缘关系，归并关系版本后形成字段路径；同一字段只保留一个可执行目标。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/lineage">返回血缘关系</RouterLink>
        <RouterLink class="btn" to="/flow">去流转编排复核</RouterLink>
        <button class="btn" type="button" data-testid="refresh-field-view" @click="reload">刷新视图</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="metric in metrics" :key="metric.key" class="stat-card" data-testid="field-metric">
        <span class="stat-label">{{ metric.label }}</span>
        <strong class="stat-value">{{ metric.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>业务边界</span>
        <select v-model="precedence" data-testid="precedence" @change="reload">
          <option value="verified">验证优先（已验证关系决定当前路径）</option>
          <option value="latest">最新优先（最新记录决定当前路径）</option>
        </select>
      </label>
      <label class="filter-item">
        <span>采集批次</span>
        <select v-model="batch" data-testid="batch-filter" @change="reload">
          <option value="">全部批次</option>
          <option v-for="item in batches" :key="item['采集批次']" :value="item['采集批次']">
            {{ item['采集批次'] }} · {{ item['批次版本'] }}
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>关系版本</span>
        <select v-model="version" data-testid="version-filter" @change="reload">
          <option value="">全部版本</option>
          <option value="v1">v1</option>
          <option value="v2">v2</option>
        </select>
      </label>
      <span class="snapshot-tag" data-testid="field-snapshot">快照 {{ view['快照版本'] || '—' }}</span>
      <span class="snapshot-tag">边界：{{ view.precedence_label }}</span>
    </form>

    <div class="view-grid">
      <section class="view-card">
        <h3>汇总</h3>
        <p class="view-note">输入：{{ (view.generated_from || []).join(' + ') || '血缘关系记录 + 采集批次版本' }}</p>
        <ul class="summary-list">
          <li v-for="(value, label) in view.summary || {}" :key="label">
            <span>{{ label }}</span><strong>{{ value }}</strong>
          </li>
        </ul>
        <p class="view-note">台账：待复核 {{ view.review?.['待复核'] ?? 0 }} · 已复核 {{ view.review?.['已复核'] ?? 0 }}</p>
      </section>
      <section class="view-card">
        <h3>字段目标唯一性</h3>
        <p class="view-note">每个「下游节点 + 目标字段」只允许一个可执行关系，避免回查混入两个转换结果。</p>
        <table class="data-table">
          <thead><tr><th>下游节点</th><th>目标字段</th><th>可执行关系</th><th>可执行目标数</th></tr></thead>
          <tbody>
            <tr v-for="target in fieldTargets" :key="`${target['下游节点']}-${target['目标字段']}`">
              <td>{{ target['下游节点'] }}</td>
              <td>{{ target['目标字段'] }}</td>
              <td>{{ target['可执行关系'] }}</td>
              <td>{{ target['可执行目标数'] }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <section v-if="conflicts.length" class="view-card" data-testid="conflict-list">
      <h3>关系版本差异</h3>
      <ul class="conflict-list">
        <li v-for="(conflict, index) in conflicts" :key="index">
          <span class="field-flag warn">{{ conflict['冲突类型'] }}</span>
          {{ conflict['上游节点'] }}.{{ conflict['来源字段'] }} · 候选 {{ conflict['候选关系数'] }} 条 ·
          当前保留 {{ conflict['当前关系编号'] || '—' }} · 其余版本作为差异保留
        </li>
      </ul>
    </section>

    <FieldPathPanel :paths="paths" :precedence="precedence" />

    <footer class="page-foot">
      <span>{{ paths.length }} 条字段路径 · {{ view.relation_count ?? 0 }} 条关系记录参与解析</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { fetchJson } from '@/api/client'
import FieldPathPanel from '@/components/FieldPathPanel.vue'

type Metric = { key: string; label: string; value: number }
type FieldView = {
  snapshot: string
  '快照版本': string
  precedence: string
  precedence_label: string
  generated_from?: string[]
  metrics?: Metric[]
  summary?: Record<string, number>
  review?: Record<string, number>
  paths?: any[]
  conflicts?: any[]
  field_targets?: any[]
  batches?: any[]
  relation_count?: number
}

const view = ref<FieldView>({ snapshot: '', '快照版本': '', precedence: 'verified', precedence_label: '验证优先' })
const route = useRoute()
const precedence = ref(String(route.query.precedence || 'verified'))
const batch = ref('')
const version = ref('')
const errorMessage = ref('')

const metrics = computed<Metric[]>(() => view.value.metrics || [])
const paths = computed<any[]>(() => view.value.paths || [])
const conflicts = computed<any[]>(() => view.value.conflicts || [])
const fieldTargets = computed<any[]>(() => view.value.field_targets || [])
const batches = computed<any[]>(() => view.value.batches || [])

async function reload() {
  const query = new URLSearchParams()
  query.set('precedence', precedence.value)
  if (batch.value) query.set('batch', batch.value)
  if (version.value) query.set('version', version.value)
  try {
    view.value = await fetchJson<FieldView>(`/api/lineage/field-view?${query.toString()}`)
    errorMessage.value = ''
  } catch {
    errorMessage.value = '字段级血缘视图加载失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.filter-item select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.snapshot-tag { font-size: 12px; color: var(--muted); background: #eef2f7; border-radius: 10px; padding: 4px 10px; }
.view-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.view-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.view-card h3 { margin: 0 0 6px; font-size: 14px; }
.view-note { font-size: 12px; color: var(--muted); margin: 4px 0 8px; }
.summary-list { list-style: none; margin: 0; padding: 0; }
.summary-list li { display: flex; justify-content: space-between; border-bottom: 1px solid #f0f2f5; padding: 6px 0; font-size: 13px; }
.conflict-list { margin: 0; padding-left: 18px; font-size: 12px; }
.conflict-list li { margin-bottom: 6px; }
.field-flag { font-size: 11px; border-radius: 10px; padding: 1px 8px; background: #eef2f7; color: #475569; }
.field-flag.warn { background: #fef3c7; color: #92400e; }
</style>
