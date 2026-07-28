import { describe, expect, it } from "vitest";

import {
  edgeColors,
  MAF_NODE_TYPE_OPTIONS,
  nodeColors,
} from "./graph-constants";

describe("agent graph taxonomy", () => {
  it("defines a visual treatment for every MAF node type", () => {
    for (const nodeType of MAF_NODE_TYPE_OPTIONS) {
      expect(nodeColors[nodeType], nodeType).toBeDefined();
    }
  });

  it("covers canonical approval and execution relationships", () => {
    for (const relationship of [
      "has_execution",
      "executes",
      "requires_approval",
      "approved_by",
      "denied_by",
      "modified_by",
    ]) {
      expect(edgeColors[relationship], relationship).toBeDefined();
    }
  });
});
