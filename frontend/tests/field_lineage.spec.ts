import { expect, test } from '@playwright/test'

test('字段级血缘视图：汇总入口、路径展开、版本切换与差异', async ({ page }) => {
  test.setTimeout(180_000)
  const browserErrors: string[] = []
  page.on('pageerror', (e) => browserErrors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`)
  })

  await page.goto('/lineage')
  await expect(page.getByTestId('field-view-preview')).toBeVisible()
  await expect(page.getByTestId('field-view-detail-entry')).toBeVisible()

  await page.getByTestId('field-view-detail-entry').click()
  await expect(page).toHaveURL(/\/lineage\/field-view/)
  await expect(page.getByTestId('field-snapshot')).toContainText('SNAP-')
  const metrics = page.getByTestId('field-metric')
  expect(await metrics.count()).toBeGreaterThanOrEqual(4)
  await expect(page.getByTestId('conflict-list')).toBeVisible()

  // 每个「下游节点 + 目标字段」只有一条可执行关系
  const executableRows = page.locator('section.view-card').filter({ hasText: '字段目标唯一性' }).locator('tbody tr')
  const rowCount = await executableRows.count()
  expect(rowCount).toBeGreaterThan(0)
  for (let i = 0; i < rowCount; i++) {
    await expect(executableRows.nth(i).locator('td').nth(3)).toHaveText('1')
  }

  // 找到带旧转换说明的字段路径，展开后应能看到提示与版本 chip
  const cards = page.locator('[data-path-id]')
  expect(await cards.count()).toBeGreaterThanOrEqual(5)

  const orderPath = page.locator('[data-path-id]').filter({ hasText: '订单采集通道.订单号' }).first()
  await expect(orderPath).toBeVisible()
  await orderPath.getByTestId(/toggle-path-/).click()
  const pathId = await orderPath.getAttribute('data-path-id')
  await expect(page.getByTestId(`path-body-${pathId}`)).toBeVisible()
  await expect(page.getByTestId('stale-flag').first()).toBeVisible()

  // 当前为 v1（已验证优先），切换到 v2 查看差异
  await page.getByTestId(`version-${pathId}-v2`).click()
  const diff = page.getByTestId(`diff-${pathId}`)
  await expect(diff).toContainText('差异')
  await expect(diff).toContainText('关系版本')

  // 切到最新优先后，当前版本变为 v2，旧路径不再标记沿用旧转换说明
  await page.getByTestId('precedence').selectOption('latest')
  await expect(page.getByTestId('field-snapshot')).toContainText('SNAP-')
  await expect(page.getByTestId('field-snapshot')).not.toHaveText(/SNAP-37905E487DB0/)
  const latestPath = page.locator('[data-path-id]').filter({ hasText: '订单采集通道.订单号' }).first()
  await expect(latestPath).toContainText('版本 v2')
  await expect(latestPath).not.toContainText('沿用旧转换说明')

  expect(browserErrors.filter((e) => !e.includes('409'))).toEqual([])
})

test('流转编排读取同一快照：节点顺序、台账同步一次成功、核对写回', async ({ page }) => {
  test.setTimeout(180_000)
  const browserErrors: string[] = []
  page.on('pageerror', (e) => browserErrors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400 && r.status() !== 409) {
      browserErrors.push(`${r.status()} ${r.url()}`)
    }
  })

  await page.goto('/flow')
  await expect(page.getByTestId('flow-field-view')).toBeVisible()
  await expect(page.getByTestId('flow-snapshot')).toContainText('SNAP-')
  const orderItems = page.locator('[data-testid="node-order"] li')
  await expect(orderItems.first()).toBeVisible()
  const orderText = await orderItems.allInnerTexts()
  // 上游采集通道必须排在解析器/入库之前
  expect(orderText.findIndex((t) => t.includes('订单采集通道'))).toBeLessThan(
    orderText.findIndex((t) => t.includes('订单入库')),
  )
  await expect(page.getByTestId('open-field-view')).toBeVisible()

  const existingLedger = page.locator('[data-testid^="ledger-"]')
  if ((await existingLedger.count()) === 0) {
    await page.getByTestId('sync-ledger').click()
    await expect(page.getByTestId('flow-message')).toBeVisible()
    await expect(page.locator('[data-testid^="ledger-"]').first()).toBeVisible()
  }

  // 再次同步同一快照：只允许一次成功，其余拒绝
  await page.getByTestId('sync-ledger').click()
  await expect(page.getByTestId('flow-message')).toContainText(/同快照|仅一次|已同步/)

  const pendingEntry = page.locator('[data-review-status="待复核"]').first()
  if ((await pendingEntry.count()) > 0) {
    await pendingEntry.getByTestId('review-diff').click()
    await expect(page.getByTestId('flow-message')).toContainText('核对结果已写入字段级血缘视图')
    await expect(page.locator('[data-testid^="ledger-"]').filter({ hasText: '已复核' }).first()).toBeVisible()

    // 血缘页应看到同一份视图里的核对结果
    await page.goto('/lineage/field-view')
    await expect(page.locator('.field-flag.ok').first()).toContainText('已复核')
  }

  expect(browserErrors).toEqual([])
})
