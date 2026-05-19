import { expect, test } from '@playwright/test';

test('unified chat: new chat, send message, trace and save segment action, reload /chat/:id', async ({ page }) => {
  const sessionId = 'chat-session-1';
  const createdSessionId = 'chat-session-created';
  const now = '2026-05-19T09:00:00.000Z';
  let callCount = 0;

  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: createdSessionId, title: 'Новый чат', status: 'collect_brief', updated_at: now, last_message_preview: '' }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ id: sessionId, title: 'Demo unified chat', status: 'ok', updated_at: now, last_message_preview: 'trace' }]),
    });
  });

  await page.route(`**/api/sessions/${sessionId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: sessionId,
        title: 'Demo unified chat',
        status: 'ok',
        updated_at: now,
        messages: [
          { id: 'm1', role: 'assistant', content: 'trace step 1', created_at: now },
          { id: 'm2', role: 'assistant', content: JSON.stringify({ type: 'action_card', action_id: 'save_segment', title: 'Сохранить сегмент', explanation: 'Сохранить сегмент в AdTarget' }), created_at: now },
        ],
      }),
    });
  });

  await page.route('**/api/chat', async (route) => {
    callCount += 1;
    const body = route.request().postDataJSON();
    if (body?.action?.id === 'save_segment') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ assistant_message: 'done', artifacts: [{ id: 'seg-1', type: 'segment', title: 'segment', content: {} }], actions_available: [{ id: 'next', label: 'next' }] }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
  });

  await page.goto('/chat');
  await expect(page.getByText('Demo unified chat')).toBeVisible();

  await page.getByRole('button', { name: 'New chat' }).click();
  await expect(page).toHaveURL(`/chat/${createdSessionId}`);

  await page.goto(`/chat/${sessionId}`);
  await expect(page.getByText('trace step 1')).toBeVisible();

  await page.getByPlaceholder('Введите сообщение').fill('Привет');
  await page.getByRole('button', { name: 'Отправить' }).click();
  expect(callCount).toBeGreaterThan(0);

  await page.getByRole('button', { name: 'Сохранить' }).click();
  await expect(page.getByText('✅ Сохранено. artifact_id:')).toBeVisible();

  await page.reload();
  await expect(page).toHaveURL(`/chat/${sessionId}`);
  await expect(page.getByText('trace step 1')).toBeVisible();
});

test('unified chat: empty list and backend error state', async ({ page }) => {
  await page.route('**/api/sessions', async (route) => {
    await route.fulfill({ status: 500, body: 'oops' });
  });

  await page.goto('/chat');
  await expect(page.getByText('Не удалось загрузить историю чатов')).toBeVisible();
});
