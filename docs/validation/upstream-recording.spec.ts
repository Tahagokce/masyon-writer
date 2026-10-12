import { test, expect, type Page } from '@playwright/test';

// Actual Playwright MCP action code; see the accompanying run report for provenance.
test('recorded Jira case', async ({ page, context }) => {
  // browser_navigate
  await page.goto('http://127.0.0.1:53303');
  // browser_type
  await page.getByRole('textbox', { name: 'Username' }).fill(process.env['WBS_USERNAME']!);
  // browser_type
  await page.getByRole('textbox', { name: 'Password' }).fill(process.env['WBS_PASSWORD']!);
  // browser_click
  await page.getByRole('button', { name: 'Log in' }).click();
  // browser_assert
  await(async (page: Page) => {
      const target = page.getByRole('heading', { name: 'Dashboard' });
      const deadline = Date.now() + 5000;
      while (true) {
          if (await target.isVisible())
              return { verified: true };
          if (Date.now() >= deadline)
              throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
      }
  })(page);
  // browser_click
  await page.getByRole('link', { name: 'Flights menu' }).click();
  // browser_assert
  await(async (page: Page) => {
      const target = page.getByRole('heading', { name: 'Flights' });
      const deadline = Date.now() + 5000;
      while (true) {
          if (await target.isVisible())
              return { verified: true };
          if (Date.now() >= deadline)
              throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
      }
  })(page);
  // browser_click
  const opened14: Page[] = [];
  const capture14 = (p: Page) => opened14.push(p);
  context.on('page', capture14);
  await page.getByRole('link', { name: 'Demo Flight' }).click();
  await expect.poll(() => opened14.length).toBe(1);
  context.off('page', capture14);
  const page1 = opened14[0];
  expect(await page1.opener()).toBe(page);
  // browser_assert
  await(async (page1: Page) => {
      const target = page1.getByRole('heading', { name: 'Flight details' });
      const deadline = Date.now() + 5000;
      while (true) {
          if (await target.isVisible())
              return { verified: true };
          if (Date.now() >= deadline)
              throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
      }
  })(page1);
  // browser_type
  await page1.getByRole('textbox', { name: 'SL Code' }).fill('AUTO_CONTRACT_001');
  // browser_press_key
  // Press Tab
  await page1.keyboard.press('Tab');
  // browser_select_option
  await page1.getByLabel('Status').selectOption('Ready');
  // browser_assert
  await(async (page1: Page) => {
      const target = page1.getByLabel('Status');
      const deadline = Date.now() + 5000;
      while (true) {
          if ((await target.inputValue()) === "Ready")
              return { verified: true };
          if (Date.now() >= deadline)
              throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
      }
  })(page1);
  // browser_click
  await page1.getByRole('button', { name: 'Save' }).click();
  // browser_assert
  await(async (page1: Page) => {
      const target = page1.locator('#codes');
      const deadline = Date.now() + 5000;
      while (true) {
          if ((await target.innerText()).includes("AUTO_CONTRACT_001"))
              return { verified: true };
          if (Date.now() >= deadline)
              throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
      }
  })(page1);
  // browser_click
  page1.once('dialog', dialog => { void dialog.accept(""); });
  await page1.getByRole('button', { name: 'Confirm' }).click();
  // browser_assert
  await(async (page1: Page) => {
      const target = page1.locator('#confirmed');
      const deadline = Date.now() + 5000;
      while (true) {
          if ((await target.innerText()).includes("Confirmed"))
              return { verified: true };
          if (Date.now() >= deadline)
              throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
      }
  })(page1);
  // browser_assert
  await(async (page: Page) => {
      const target = page.getByRole('heading', { name: 'Flights' });
      const deadline = Date.now() + 5000;
      while (true) {
          if (await target.isVisible())
              return { verified: true };
          if (Date.now() >= deadline)
              throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
      }
  })(page);
  // browser_new_page
  const opened34: Page[] = [];
  const capture34 = (p: Page) => opened34.push(p);
  context.on('page', capture34);
  await context.newPage();
  await expect.poll(() => opened34.length).toBe(1);
  context.off('page', capture34);
  const page2 = opened34[0];
  // browser_navigate
  await page2.goto('http://127.0.0.1:53303');
  // browser_assert
  await(async (page2: Page) => {
      const target = page2.getByRole('button', { name: 'Log in' });
      const deadline = Date.now() + 5000;
      while (true) {
          if (await target.isVisible())
              return { verified: true };
          if (Date.now() >= deadline)
              throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
      }
  })(page2);
  // browser_close_page
  await page2.close();
});
