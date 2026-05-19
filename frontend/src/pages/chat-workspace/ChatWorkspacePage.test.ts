import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { groupByUpdatedAt } from './ChatWorkspacePage';

describe('ChatWorkspacePage component helpers', () => {
  it('groups chat sessions by today / yesterday / earlier', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-19T12:00:00.000Z'));

    const grouped = groupByUpdatedAt([
      { id: '1', title: 'today', status: 'ok', updatedAt: '2026-05-19T09:00:00.000Z', lastMessagePreview: '' },
      { id: '2', title: 'yesterday', status: 'ok', updatedAt: '2026-05-18T09:00:00.000Z', lastMessagePreview: '' },
      { id: '3', title: 'old', status: 'ok', updatedAt: '2026-05-10T09:00:00.000Z', lastMessagePreview: '' },
    ]);

    expect(grouped['Сегодня'].map((x) => x.id)).toEqual(['1']);
    expect(grouped['Вчера'].map((x) => x.id)).toEqual(['2']);
    expect(grouped['Ранее'].map((x) => x.id)).toEqual(['3']);

    vi.useRealTimers();
  });

  it('handles empty session list', () => {
    const grouped = groupByUpdatedAt([]);
    expect(grouped['Сегодня']).toEqual([]);
    expect(grouped['Вчера']).toEqual([]);
    expect(grouped['Ранее']).toEqual([]);
  });

  it('does not render mode switcher select in unified dialog UX', () => {
    const source = readFileSync(new URL('./ChatWorkspacePage.tsx', import.meta.url), 'utf-8');
    expect(source.includes('chat-context-switcher')).toBe(true);
    expect(source.includes('<select')).toBe(false);
  });

  it('loads widget shell styles only in widget mode', () => {
    const source = readFileSync(new URL('../../main.tsx', import.meta.url), 'utf-8');
    expect(source.includes('void import("./styles/widget-shell.css")')).toBe(true);
    expect(source.includes('window.location.pathname.startsWith("/widget")')).toBe(true);
    expect(source.includes('floating-widget-root')).toBe(true);
  });

  it('does not statically import widget shell styles in full-page mode', () => {
    const indexCss = readFileSync(new URL('../../index.css', import.meta.url), 'utf-8');
    expect(indexCss.includes('@import "./styles/widget-shell.css";')).toBe(false);
  });
});
