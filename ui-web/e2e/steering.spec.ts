import { test, expect } from './fixtures'

for (const queued of [false, true]) {
  test(`Send now adds ${queued ? 'a queued prompt' : 'the draft'} to the active turn`, async ({ page, app }, testInfo) => {
    const { methods, events } = app
    const composer = page.getByRole('textbox', { name: 'Message ClawCodex' })
    const trigger = queued ? 'E2E_STEER_TOOL_TRIGGER' : 'E2E_STEER_TRIGGER'
    await composer.fill(`${trigger} inspect the project`)
    await composer.press('Enter')
    await expect.poll(app.isSteeringHeld).toBe(true)
    await composer.fill('does it support complex functions?')
    if (queued) {
      await composer.press('Enter')
      await expect(page.getByRole('button', { name: 'Remove from queue' })).toBeVisible()
      await expect(composer).toHaveValue('')
    }
    await page.screenshot({ path: testInfo.outputPath('send-now-button.png') })
    await page.setViewportSize({ width: 620, height: 800 })
    const sendNow = page.getByRole('button', { name: 'Send now', exact: true })
    await expect(sendNow).toBeInViewport()
    await page.screenshot({ path: testInfo.outputPath('send-now-narrow.png') })
    await page.getByRole('button', { name: 'Send now', exact: true }).click()
    await expect(page.getByText('Sent to the active turn. The agent will read it after the current step.', { exact: true })).toBeVisible()
    await expect(composer).toHaveValue('')
    await expect(page.getByRole('button', { name: 'Remove from queue' })).toHaveCount(0)
    expect(events).not.toContain('message.complete')
    expect(methods.filter(method => method === 'prompt.submit')).toHaveLength(1)
    expect(methods.filter(method => method === 'session.steer')).toHaveLength(1)
    expect(methods).not.toContain('session.interrupt')
    app.releaseSteering()
    await expect(page.getByText('The original task and complex functions are handled together.', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0)
    expect(app.received.some(input => input.includes(trigger) && input.includes('complex functions'))).toBe(true)
    expect(events.filter(event => event === 'message.complete')).toHaveLength(1)
    expect(events.filter(event => event === 'message.steered')).toHaveLength(1)
    if (queued) expect(events).toContain('tool.complete')
    await page.setViewportSize({ width: 1440, height: 960 })
    await page.screenshot({ path: testInfo.outputPath('steered-conversation.png') })
    await page.reload()
    await expect(page.getByText('does it support complex functions?', { exact: true })).toBeVisible()
    await expect(page.getByText('The original task and complex functions are handled together.', { exact: true })).toBeVisible()
  })
}
