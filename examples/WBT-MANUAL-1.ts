import { test, expect } from '@playwright/test';

test('WBT-MANUAL-1', async ({ page }) => {
  await page.locator('#special-load-tab').click();
  await page.locator('#sl-code').fill('TSX');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
});
