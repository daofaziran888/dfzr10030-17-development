<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <section class="field-overview" data-testid="overview-field-lineage">
      <header class="field-overview-head">
        <div>
          <h3>字段级血缘视图</h3>
          <p class="page-desc">概览只列汇总；沿字段路径查看当前版本与历史差异请进入看板明细。</p>
        </div>
        <div class="page-actions">
          <RouterLink class="btn" to="/lineage/field-view" data-testid="overview-field-view-entry">查看字段路径明细</RouterLink>
          <RouterLink class="btn" to="/flow" data-testid="overview-flow-entry">去流转编排</RouterLink>
        </div>
      </header>
      <div class="stat-row" style="margin-bottom:0">
        <article v-for="metric in fieldMetrics" :key="metric.key" class="stat-card">
          <span class="stat-label">{{ metric.label }}</span>
          <strong class="stat-value">{{ metric.value }}</strong>
        </article>
      </div>
      <p class="page-desc" data-testid="overview-field-snapshot">
        快照 {{ fieldSnapshot || '—' }} · {{ fieldPrecedenceLabel || '验证优先' }} ·
        冲突 {{ (fieldConflicts || []).length }} 项 · 待复核 {{ fieldReview?.['待复核'] ?? 0 }} 条
      </p>
    </section>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const fieldMetrics = ref<{ key: string; label: string; value: number }[]>([])
const fieldSnapshot = ref('')
const fieldPrecedenceLabel = ref('')
const fieldConflicts = ref<any[]>([])
const fieldReview = ref<Record<string, number>>({})

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "数据源配置", "created": 0, "pending": 0, "abnormal": 0}, {"name": "网页采集规则", "created": 0, "pending": 0, "abnormal": 0}, {"name": "API接入管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "增量采集", "created": 0, "pending": 0, "abnormal": 0}, {"name": "RPA任务编排", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据接入通道", "created": 0, "pending": 0, "abnormal": 0}, {"name": "格式解析器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集结果校验", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集去重", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集调度", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集监控", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集日志", "created": 0, "pending": 0, "abnormal": 0}, {"name": "凭证管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集转换管道", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据流转编排", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据血缘追踪", "created": 0, "pending": 0, "abnormal": 0}, {"name": "采集报告", "created": 0, "pending": 0, "abnormal": 0}]
  }
  try {
    const summary = await fetchJson<any>('/api/lineage/field-view/summary?precedence=verified')
    fieldMetrics.value = summary.metrics || []
    fieldSnapshot.value = summary.snapshot || ''
    fieldPrecedenceLabel.value = summary.precedence_label || ''
    fieldConflicts.value = summary.conflicts || []
    fieldReview.value = summary.review || {}
  } catch {
    fieldMetrics.value = []
  }
})
</script>
<style scoped>
.field-overview { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.field-overview-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.field-overview-head h3 { margin: 0; font-size: 15px; }
</style>
