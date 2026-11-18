<template>
  <section class="field-paths" data-testid="field-path-panel">
    <header class="field-paths-head">
      <div>
        <h3>字段级血缘路径</h3>
        <p class="page-desc">每条路径绑定关系版本与采集批次；同一字段在当前视图中只保留一个可执行目标。</p>
      </div>
      <span class="path-count">{{ paths.length }} 条字段路径</span>
    </header>
    <p v-if="!paths.length" class="empty-state">当前快照下没有可解析的字段路径</p>
    <article
      v-for="path in paths"
      :key="path.path_id"
      class="field-path-card"
      :class="{ 'has-diff': path['存在版本差异'] }"
      :data-testid="`field-path-${path.path_id}`"
      :data-path-id="path.path_id"
    >
      <button class="field-path-summary" type="button" :data-testid="`toggle-path-${path.path_id}`" @click="toggle(path)">
        <span class="field-path-title">{{ path['字段路径'] }}</span>
        <span class="field-tag">版本 {{ path['关系版本'] }}</span>
        <span class="field-tag">批次 {{ path['采集批次'] || '—' }}</span>
        <span class="field-tag" :class="statusClass(path['关系状态'])">{{ path['关系状态'] }}</span>
        <span v-if="path['沿用旧转换说明']" class="field-flag warn" data-testid="stale-flag">沿用旧转换说明</span>
        <span v-else-if="path['存在版本差异']" class="field-flag">存在版本差异</span>
        <span v-if="path['复核状态'] === '已复核'" class="field-flag ok">已复核：{{ path['复核结果'] }}</span>
        <span class="field-path-toggle">{{ expanded[path.path_id] ? '收起' : '展开' }}</span>
      </button>

      <div v-if="expanded[path.path_id]" class="field-path-body" :data-testid="`path-body-${path.path_id}`">
        <div class="field-path-meta">
          <span>快照 {{ path['快照'] }}</span>
          <span>上游 {{ path['上游节点'] }} · 来源字段 {{ path['来源字段'] }}</span>
          <span>下游 {{ path['下游节点'] }} · 目标字段 {{ path['目标字段'] }}</span>
          <span v-if="path['冲突数']">冲突 {{ path['冲突数'] }} 处</span>
        </div>

        <ol class="field-steps">
          <li v-for="step in path['步骤']" :key="step['关系编号']" class="field-step" :data-testid="`step-${path.path_id}-${step['步骤序号']}`">
            <div class="field-step-line">
              <strong>{{ step['步骤序号'] }}. {{ step['上游节点'] }}.{{ step['来源字段'] }} → {{ step['下游节点'] }}.{{ step['目标字段'] }}</strong>
              <span v-if="!step['可执行']" class="field-flag warn">目标已被 {{ step['被占用目标'] }} 占用</span>
            </div>
            <div class="field-step-detail">
              <span>关系 {{ step['关系编号'] }}</span>
              <span>版本 {{ step['关系版本'] }}</span>
              <span>批次 {{ step['采集批次'] }}</span>
              <span>转换 {{ step['转换说明'] }}</span>
              <span :class="['field-status', statusClass(step['关系状态'])]">{{ step['关系状态'] }}</span>
              <span v-if="step['沿用旧转换说明']" class="field-flag warn">沿用旧转换说明</span>
            </div>
            <div class="field-versions">
              <span class="field-versions-label">关系版本</span>
              <button
                v-for="version in step['版本列表']"
                :key="version['关系编号']"
                type="button"
                class="version-chip"
                :class="{ active: version['关系版本'] === activeVersion(path.path_id) }"
                :data-testid="`version-${path.path_id}-${version['关系版本']}`"
                @click.stop="selectVersion(path, version['关系版本'])"
              >
                {{ version['关系版本'] }}
                <em v-if="version['是否当前']">当前</em>
                <em v-if="version['是否最新记录'] && !version['是否当前']">最新记录</em>
              </button>
            </div>
          </li>
        </ol>

        <div class="field-path-actions">
          <label class="field-inline">
            <span>对比版本</span>
            <select v-model="compareVersion[path.path_id]" :data-testid="`compare-${path.path_id}`" @change="selectVersion(path, compareVersion[path.path_id])">
              <option value="">当前版本</option>
              <option v-for="version in versionsOf(path)" :key="version" :value="version">{{ version }}</option>
            </select>
          </label>
          <span class="field-inline-note">选中版本后，路径步骤按该版本重放并与当前版本对比</span>
        </div>

        <div class="field-diff" :data-testid="`diff-${path.path_id}`">
          <p v-if="loading[path.path_id]">正在读取版本差异…</p>
          <template v-else-if="diffs[path.path_id]">
            <strong>{{ diffs[path.path_id].summary }}</strong>
            <ul v-if="diffs[path.path_id].changes.length">
              <li v-for="(change, index) in diffs[path.path_id].changes" :key="index">
                步骤 {{ change['步骤'] }} · {{ change['字段路径'] }}：{{ change['项目'] }}
                {{ change['当前版本'] || '—' }} → {{ change['对比版本'] || '—' }}
              </li>
            </ul>
          </template>
          <p v-else class="empty-state">展开后选择版本即可查看差异</p>
        </div>

        <ul v-if="path['冲突'] && path['冲突'].length" class="field-conflicts">
          <li v-for="(conflict, index) in path['冲突']" :key="index">
            <span class="field-flag warn">{{ conflict['冲突类型'] }}</span>
            来源字段 {{ conflict['来源字段'] }}：{{ conflict['候选关系数'] }} 条候选关系，当前保留 {{ conflict['当前关系编号'] }}，
            其余作为差异保留（{{ (conflict['候选目标字段'] || []).join(' / ') }}）
          </li>
        </ul>
      </div>
    </article>
  </section>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'

import { fetchJson } from '@/api/client'

const props = defineProps<{
  paths: any[]
  endpoint?: string
  precedence?: string
}>()

const expanded = reactive<Record<string, boolean>>({})
const compareVersion = reactive<Record<string, string>>({})
const selectedVersion = reactive<Record<string, string>>({})
const diffs = reactive<Record<string, any>>({})
const loading = reactive<Record<string, boolean>>({})

const base = () => props.endpoint || '/api/lineage/field-view/paths'
const precedenceQuery = () => (props.precedence ? `?precedence=${props.precedence}` : '')

const statusClass = (status: string) => ({
  'status-verified': status === '已验证',
  'status-recorded': status === '已记录',
  'status-draft': status === '草稿',
  'status-archived': status === '已归档',
})

const activeVersion = (pathId: string) => selectedVersion[pathId] || ''
const versionsOf = (path: any) => (path['版本列表'] || []).map((item: any) => item['关系版本'])

function toggle(path: any) {
  expanded[path.path_id] = !expanded[path.path_id]
  if (expanded[path.path_id] && !diffs[path.path_id]) loadDiff(path)
}

function selectVersion(path: any, version: string) {
  selectedVersion[path.path_id] = version
  compareVersion[path.path_id] = version
  loadDiff(path)
}

async function loadDiff(path: any) {
  const pathId = path.path_id
  // 左侧固定为当前版本（不传 left），右侧为选中的关系版本。
  const right = compareVersion[pathId] || ''
  loading[pathId] = true
  try {
    const query = new URLSearchParams()
    if (right) query.set('right', right)
    if (props.precedence) query.set('precedence', props.precedence)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    diffs[pathId] = await fetchJson(`${base()}/${pathId}/diff${suffix}`)
  } catch {
    diffs[pathId] = { summary: '版本差异读取失败', changes: [] }
  } finally {
    loading[pathId] = false
  }
}

watch(
  () => props.precedence,
  () => {
    Object.keys(diffs).forEach((key) => delete diffs[key])
  },
)

onMounted(() => {
  props.paths.forEach((path: any) => {
    if (path['沿用旧转换说明']) expanded[path.path_id] = true
  })
})
</script>

<style scoped>
.field-paths { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-top: 12px; }
.field-paths-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.field-paths-head h3 { margin: 0; font-size: 15px; }
.path-count { font-size: 12px; color: var(--muted); }
.field-path-card { border: 1px solid var(--border); border-radius: 8px; margin-top: 10px; overflow: hidden; }
.field-path-card.has-diff { border-color: #f0b429; }
.field-path-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; width: 100%; padding: 10px 12px; background: #fafbfc; border: none; cursor: pointer; text-align: left; }
.field-path-title { font-weight: 600; font-size: 13px; }
.field-path-toggle { margin-left: auto; color: var(--brand); font-size: 12px; }
.field-tag { font-size: 11px; color: #475569; background: #eef2f7; border-radius: 10px; padding: 1px 8px; }
.field-flag { font-size: 11px; border-radius: 10px; padding: 1px 8px; background: #eef2f7; color: #475569; }
.field-flag.warn { background: #fef3c7; color: #92400e; }
.field-flag.ok { background: #d1fae5; color: #065f46; }
.field-status { font-size: 11px; border-radius: 10px; padding: 1px 8px; background: #e2e8f0; }
.status-verified { background: #d1fae5; color: #065f46; }
.status-recorded { background: #e0e7ff; color: #3730a3; }
.status-draft { background: #e2e8f0; color: #334155; }
.status-archived { background: #e5e7eb; color: #6b7280; }
.field-path-body { padding: 10px 12px 12px; border-top: 1px solid var(--border); }
.field-path-meta { display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.field-steps { margin: 0; padding-left: 18px; }
.field-step { margin-bottom: 10px; font-size: 13px; }
.field-step-line { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.field-step-detail { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: var(--muted); margin-top: 3px; }
.field-versions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
.field-versions-label { font-size: 12px; color: var(--muted); }
.version-chip { border: 1px solid var(--border); background: #fff; border-radius: 12px; padding: 2px 10px; font-size: 12px; cursor: pointer; }
.version-chip.active { border-color: var(--brand); color: var(--brand); }
.version-chip em { font-style: normal; font-size: 10px; margin-left: 4px; color: var(--muted); }
.field-path-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-top: 8px; }
.field-inline { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.field-inline select { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; }
.field-inline-note { font-size: 12px; color: var(--muted); }
.field-diff { margin-top: 8px; padding: 8px 10px; background: #f8fafc; border-radius: 6px; font-size: 12px; }
.field-diff ul { margin: 6px 0 0; padding-left: 18px; }
.field-conflicts { margin: 8px 0 0; padding-left: 18px; font-size: 12px; color: #92400e; }
.field-conflicts li { margin-bottom: 4px; }
</style>
