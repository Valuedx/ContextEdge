import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// React Testing Library only self-registers `afterEach(cleanup)` when a
// global `afterEach` exists, and this project runs vitest without
// `globals: true`. Without it the jsdom document accumulates every
// render in a file, so a later `queryByText(...).not.toBeInTheDocument()`
// asserts against markup an earlier test left behind — it fails when the
// component is right, and worse, passes when it is wrong.
afterEach(cleanup);
