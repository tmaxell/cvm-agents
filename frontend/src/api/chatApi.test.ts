import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getChat, listChats } from './chatApi';

describe('chatApi integration-ish', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('handles empty DB sessions response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ sessions: [] }), { status: 200 })));
    await expect(listChats()).resolves.toEqual([]);
  });

  it('retries on slow/failing response and returns normalized data', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('timeout'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 's1', messages: [{ role: 'assistant', content: 'ok' }] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const detail = await getChat('s1');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(detail.messages[0].role).toBe('assistant');
  });

  it('returns user-friendly error on 500', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('oops', { status: 500 })));
    await expect(listChats()).rejects.toThrow('Не удалось загрузить историю чатов');
  });

  it('normalizes partially broken records', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ sessions: [{ id: null, title: null, status: null }] }), { status: 200 })));
    const sessions = await listChats();
    expect(sessions[0].title).toBe('Без названия');
    expect(typeof sessions[0].id).toBe('string');
  });
});
