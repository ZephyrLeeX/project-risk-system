import type { OpenApi } from "@risk-platform/contracts";

import { apiRequest } from "./http";

/**
 * Generated OpenAPI Agent contract types (ADRs 0019 / 0028 / 0029 / T045
 * fidelity). These aliases only re-reference the frozen generated authority;
 * they are never hand-written substitutes.
 */
export type AgentHelpResponse = OpenApi.components["schemas"]["AgentHelpResponse"];
export type AgentToolHelp = OpenApi.components["schemas"]["AgentToolHelp"];
export type AgentConversationEnvelope =
  OpenApi.components["schemas"]["AgentConversationEnvelope"];
export type AgentConversationHistory =
  OpenApi.components["schemas"]["AgentConversationHistory"];
export type AgentConversationRuntime =
  OpenApi.components["schemas"]["AgentConversationRuntime"];
export type AgentConversationResponse =
  OpenApi.components["schemas"]["AgentConversationResponse"];
export type AgentMessageResponse =
  OpenApi.components["schemas"]["AgentMessageResponse"];
export type AgentMessagePage = OpenApi.components["schemas"]["AgentMessagePage"];
export type AgentMessageEnvelope =
  OpenApi.components["schemas"]["AgentMessageEnvelope"];
export type AgentInteraction =
  OpenApi.components["schemas"]["AgentInteractionResponse"];
export type AgentInteractionResponse =
  OpenApi.components["schemas"]["AgentInteractionRespondResponse"];
export type AgentInteractionRequest =
  OpenApi.components["schemas"]["AgentInteractionRespondRequest"];

export interface AgentMessageQuery {
  afterSequence?: number;
  limit?: number;
}

export const agentApi = {
  /** Closed tool directory the caller may invoke (`GET /agent/help`). */
  async help(): Promise<AgentHelpResponse> {
    return (await apiRequest<AgentHelpResponse>("/agent/help")).data;
  },

  /** Start a conversation and enqueue the first turn (`POST /agent/conversations`). */
  async create(message: string): Promise<AgentConversationEnvelope> {
    return (
      await apiRequest<AgentConversationEnvelope>("/agent/conversations", {
        method: "POST",
        body: JSON.stringify({ message }),
      })
    ).data;
  },

  /** Append a turn to an existing conversation (`POST /agent/conversations/{id}/messages`). */
  async continueConversation(
    conversationId: string,
    message: string,
  ): Promise<AgentMessageEnvelope> {
    return (
      await apiRequest<AgentMessageEnvelope>(
        `/agent/conversations/${encodeURIComponent(conversationId)}/messages`,
        {
          method: "POST",
          body: JSON.stringify({ message }),
        },
      )
    ).data;
  },

  /** Full conversation + message history (`GET /agent/conversations/{id}`). */
  async history(conversationId: string): Promise<AgentConversationHistory> {
    return (
      await apiRequest<AgentConversationHistory>(
        `/agent/conversations/${encodeURIComponent(conversationId)}`,
      )
    ).data;
  },

  /** Cancel the live execution of a conversation (`POST /agent/conversations/{id}/cancel`). */
  async cancelConversation(
    conversationId: string,
  ): Promise<AgentConversationRuntime> {
    return (
      await apiRequest<AgentConversationRuntime>(
        `/agent/conversations/${encodeURIComponent(conversationId)}/cancel`,
        { method: "POST" },
      )
    ).data;
  },

  /** A page of messages (`GET /agent/conversations/{id}/messages`). */
  async messages(
    conversationId: string,
    query: AgentMessageQuery = {},
  ): Promise<AgentMessagePage> {
    const params = new URLSearchParams();
    if (query.afterSequence !== undefined) {
      params.set("afterSequence", String(query.afterSequence));
    }
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    const suffix = params.size ? `?${params.toString()}` : "";
    return (
      await apiRequest<AgentMessagePage>(
        `/agent/conversations/${encodeURIComponent(conversationId)}/messages${suffix}`,
      )
    ).data;
  },


  async respondInteraction(
    interactionId: string,
    body: AgentInteractionRequest,
  ): Promise<AgentInteractionResponse> {
    return (
      await apiRequest<AgentInteractionResponse>(
        `/agent/interactions/${encodeURIComponent(interactionId)}/respond`,
        { method: "POST", body: JSON.stringify(body) },
      )
    ).data;
  },
};
