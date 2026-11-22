const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

// Notifications against the real E2E server (docs/design/PWA_NOTIFICATION_DESIGN.md PN4): the bell of the application bar, /settings/notifications, and the service worker.
// No test sends a request to a push service.

const TEST_TITLE = /^(Test notification|테스트 알림)$/;

// The tests of this file use one account, and the database of the E2E server can have the notifications of an earlier test: each test starts with all notifications read.
async function readAll(page) {
  const res = await page.evaluate(() => fetch('/api/me/notifications/read', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ all: true }) }).then((r) => r.status));
  expect(res).toBe(200);
}

async function pushState(page) {
  return page.evaluate(() => fetch('/api/me/push', { credentials: 'same-origin' }).then((r) => r.json()));
}

test.describe('bell and settings page', () => {
  test.use({ serviceWorkers: 'block' });

  test('a notification shows in the bell, opens its entry address, and becomes read', async ({ page }) => {
    const errors = watchErrors(page);
    await authenticateOwner(page, 'notifybell1');
    await readAll(page);
    await page.goto('/settings/notifications', { waitUntil: 'domcontentloaded' });

    // The page is in the console shell, with the device card and one switch for each kind.
    await expect(page.locator('.console-shell')).toBeVisible();
    const device = page.locator('[data-notify-device]');
    await expect(device).toBeVisible();
    await expect(device.locator('[data-notify-device-state]')).not.toHaveAttribute('data-notify-device-state', '');
    await expect(page.locator('input[type="checkbox"][switch][data-notify-kind]')).toHaveCount(19);
    const bell = page.locator('#notifybtn');
    await expect(bell).toBeVisible();
    await expect(page.locator('#notify-count')).toBeHidden();

    // A test notification: the bell shows the count at once, and the list has the item with its text.
    await device.locator('[data-notify-action="test"]').click();
    await expect(device.locator('[data-notify-result]')).toHaveAttribute('data-state', 'ok');
    await expect(page.locator('#notify-count')).toHaveText('1');
    await bell.click();
    const item = page.locator('#notify-list .notify-item:not(.is-read)');
    await expect(item).toHaveCount(1);
    await expect(item.locator('.notify-item-title')).toHaveText(TEST_TITLE);
    await expect(item).toHaveAttribute('href', '/settings/notifications');
    await expect(page.locator('#notify-list .notify-item').first()).not.toHaveClass(/is-read/);
    await expectNoRawKeys(page);

    // A switch of a kind stops Web Push for that kind only.
    await page.keyboard.press('Escape');
    const follower = page.locator('[data-notify-kind="ap_follower"]');
    await expect(follower).toBeChecked();
    await follower.click();
    await expect(page.locator('[data-notify-channel="activitypub"] [data-notify-kind-result]')).toHaveAttribute('data-state', 'ok');
    const kinds = (await pushState(page)).kinds;
    expect(kinds.find((k) => k.kind === 'ap_follower').push).toBe(false);
    expect(kinds.find((k) => k.kind === 'ap_mention').push).toBe(true);
    await follower.click();
    await expect.poll(async () => (await pushState(page)).kinds.find((k) => k.kind === 'ap_follower').push).toBe(true);

    // The item opens its entry address and becomes read.
    await bell.click();
    await Promise.all([page.waitForURL('**/settings/notifications'), item.click()]);
    await expect(page.locator('#notifybtn')).toBeVisible();
    await expect(page.locator('#notify-count')).toBeHidden();
    await page.locator('#notifybtn').click();
    await expect(page.locator('#notify-list .notify-item').first()).toHaveClass(/is-read/);
    await expect(page.locator('#notify-list .notify-item:not(.is-read)')).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('the tab query selects the tab of the knowledge base console page', async ({ page }) => {
    await authenticateOwner(page, 'notifytab01');
    await page.goto('/settings/kb?tab=subs', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#settings-kb-tabs [role="tab"][aria-selected="true"]')).toHaveAttribute('data-tab', 'subs');
    await page.goto('/settings/kb?tab=mem', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#settings-kb-tabs [role="tab"][aria-selected="true"]')).toHaveAttribute('data-tab', 'mem');
    await page.goto('/settings/kb?tab=no_such_tab', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#settings-kb-tabs [role="tab"][aria-selected="true"]')).toHaveAttribute('data-tab', 'personal');
  });
});

test.describe('service worker', () => {
  test.use({ serviceWorkers: 'allow' });

  // The headless browser has no system notifications and no application badge. The test gives the worker the events of the browser (push, notificationclick) and records the calls that the worker makes to show a notification, to set the badge, and to open a window.
  test('a push message shows a notification, and its activation marks it read and opens the entry address', async ({ page, context, baseURL }) => {
    const origin = new URL(baseURL).origin;
    await authenticateOwner(page, 'notifypush1');
    await readAll(page);
    await page.goto('/settings/notifications', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#notifybtn')).toBeVisible();
    await page.evaluate(async () => {
      await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      await navigator.serviceWorker.ready;
    });
    // One call gives the worker one event and returns the calls that the worker made. The browser can stop and start a worker between two calls, thus each call finds the worker again and sets its own records.
    const dispatch = async (type, value) => {
      let result;
      await expect(async () => {
        const worker = context.serviceWorkers().find((w) => w.url() === origin + '/sw.js');
        expect(worker).toBeTruthy();
        result = await worker.evaluate(async ([eventType, text]) => {
          const shown = [];
          const opened = [];
          self.registration.showNotification = (title, options) => { shown.push({ title, options }); return Promise.resolve(); };
          self.clients.openWindow = (path) => { opened.push(path); return Promise.resolve(null); };
          // The headless browser has no application badge service, and a call of it stops the worker: the test records the badge values.
          const badges = [];
          Object.defineProperty(self.navigator, 'setAppBadge', { configurable: true, value: (count) => { badges.push(count); return Promise.resolve(); } });
          Object.defineProperty(self.navigator, 'clearAppBadge', { configurable: true, value: () => { badges.push(0); return Promise.resolve(); } });
          const event = new Event(eventType);
          if (eventType === 'push') event.data = { json: () => JSON.parse(text) };
          else event.notification = { data: JSON.parse(text), close() {} };
          const waits = [];
          event.waitUntil = (promise) => { waits.push(promise); };
          self.dispatchEvent(event);
          await Promise.all(waits);
          return { shown, opened, badges };
        }, [type, JSON.stringify(value)]);
      }).toPass({ timeout: 15000 });
      return result;
    };

    // The server records a notification. The page did not ask again, thus the bell has no count.
    const made = await page.evaluate(() => fetch('/api/me/push/test', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then((r) => r.json()));
    expect(made.ok).toBe(true);
    await expect(page.locator('#notify-count')).toBeHidden();

    // Push: the worker shows the notification of the message and tells the open page, and the bell shows the count without a reload.
    const payload = { id: made.id, kind: 'test', title: 'Join request', body: 'Ben asks to join Cities.', url: '/settings/kb?tab=subs', tag: 'claire-' + made.id, unread: 1 };
    const pushed = await dispatch('push', payload);
    const [shown] = pushed.shown;
    expect(pushed.badges).toEqual([1]);
    expect(shown.title).toBe('Join request');
    expect(shown.options).toMatchObject({ body: 'Ben asks to join Cities.', tag: 'claire-' + made.id, renotify: true, data: { id: made.id, url: '/settings/kb?tab=subs' } });
    await expect(page.locator('#notify-count')).toHaveText('1');

    // A message with an address of another site opens the notification settings, not that site.
    const [foreign] = (await dispatch('push', { id: 999999, title: 'X', body: '', url: 'https://evil.example/x', tag: 'claire-other', unread: 1 })).shown;
    expect(foreign.options.data.url).toBe('/settings/notifications');

    // Activation: the worker marks the notification read, tells the page (the count goes), and opens the entry address.
    const activated = await dispatch('notificationclick', shown.options.data);
    expect(activated.opened).toEqual(['/settings/kb?tab=subs']);
    expect(activated.badges).toEqual([0]);
    await expect(page.locator('#notify-count')).toBeHidden();
    const listed = await page.evaluate(() => fetch('/api/me/notifications?limit=5', { credentials: 'same-origin' }).then((r) => r.json()));
    expect(listed.unread).toBe(0);
    expect(listed.items.find((i) => i.id === made.id).read).toBe(true);
  });
});
