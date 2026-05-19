import { describe, expect, it } from 'vitest';
import { ChatApiError } from '../../api/chatApi';
import { toWorkspaceError } from './chatWorkspaceStore';

describe('chatWorkspaceStore error mapping', () => {
  it('maps ChatApiError to unified workspace error shape', () => {
    const err = new ChatApiError('Timeout', 'timeout', null, true);
    expect(toWorkspaceError('load_messages', err)).toEqual({
      scope: 'load_messages',
      message: 'Timeout',
      retryable: true,
    });
  });

  it('maps unknown errors as non-retryable', () => {
    const result = toWorkspaceError('execute_action', new Error('boom'));
    expect(result.retryable).toBe(false);
    expect(result.scope).toBe('execute_action');
  });
});


it('keeps unified error shape mode-free for context-agnostic flows', () => {
  const result = toWorkspaceError('send_message', new Error('net'));
  expect(result).toEqual({ scope: 'send_message', message: 'net', retryable: false });
});
