// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ChatApiError, getChat, listChats, listMessagesPage } from './chatApi';

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
    await expect(listChats()).rejects.toThrow('Сервис временно недоступен (5xx)');
  });

  it('classifies 422 as validation error without retries', async () => {
    const fetchMock = vi.fn(async () => new Response('bad payload', { status: 422 }));
    vi.stubGlobal('fetch', fetchMock);
    const err = await listChats().catch((e) => e);
    expect(err).toBeInstanceOf(ChatApiError);
    expect(err).toMatchObject({ kind: 'validation', retryable: false, status: 422 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('classifies network failures as retryable network errors', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network down'); }));
    await expect(getChat('s1')).rejects.toMatchObject({ kind: 'network', retryable: true });
  });

  it('normalizes partially broken records', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ sessions: [{ id: null, title: null, status: null }] }), { status: 200 })));
    const sessions = await listChats();
    expect(sessions[0].title).toBe('Без названия');
    expect(typeof sessions[0].id).toBe('string');
  });

  it('supports unstable DB payload where list is not an array', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ sessions: { broken: true } }), { status: 200 })));
    await expect(listChats()).resolves.toEqual([]);
  });

  it('supports unstable DB payload where messages is not an array', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ id: 's1', messages: null, draft_flow: { id: 'df' } }), { status: 200 })));
    const detail = await getChat('s1');
    expect(detail.messages).toEqual([]);
    expect(detail.artifacts[0]?.type).toBe('draft_flow');
  });

  it('reads cursor-based messages page payload', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ messages: [{ id: 'm-1', role: 'assistant', content: 'hello' }], next_cursor: 'cur-2', has_more: true }), { status: 200 })));
    const page = await listMessagesPage('s1', null, 25);
    expect(page.messages).toHaveLength(1);
    expect(page.nextCursor).toBe('cur-2');
    expect(page.hasMore).toBe(true);
    expect(page.cursorUnsupported).toBe(false);
  });

  it('falls back when backend does not support cursor payload', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ legacy: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 's1', messages: [{ id: 'm-1', role: 'user', content: 'old' }] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const page = await listMessagesPage('s1', null, 25);
    expect(page.cursorUnsupported).toBe(true);
    expect(page.messages[0]?.id).toBe('m-1');
  });
});
