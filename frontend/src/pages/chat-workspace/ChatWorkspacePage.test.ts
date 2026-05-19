import { describe, expect, it, vi } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
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

  it('legacy stylesheet and selector registry are detached', () => {
    const indexCss = readFileSync(new URL('../../index.css', import.meta.url), 'utf-8');
    expect(indexCss.includes('@import "./styles/legacy.css";')).toBe(false);
    expect(existsSync(new URL('../../styles/legacy.css', import.meta.url))).toBe(false);
    expect(existsSync(new URL('../../styles/legacySelectorRegistry.ts', import.meta.url))).toBe(false);
  });
});
