import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { graphApi } from "@/lib/graph-api";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

describe("graphApi", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("forwards domain and temporal scope to subgraph reads", () => {
    graphApi.subgraph("session", "node-id", 2, {
      domainId: "domain-id",
      asOf: "2026-07-28T10:00:00.000Z",
    });

    expect(api.get).toHaveBeenCalledWith("/graph/subgraph/session/node-id", {
      domain_id: "domain-id",
      as_of: "2026-07-28T10:00:00.000Z",
      max_depth: "2",
    });
  });

  it("posts the versioned agent subset request unchanged", () => {
    const request = {
      query: "payment workflow",
      profile: "maf.v1",
      domain_id: "domain-id",
    };

    graphApi.agentSubset(request);

    expect(api.post).toHaveBeenCalledWith("/graph/agent-subsets", request);
  });
});
