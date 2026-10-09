import { describe, expect, it } from "vitest";

import nextConfig, { CONTENT_SECURITY_POLICY } from "../next.config";

describe("security headers", () => {
  it("are sent on every page", async () => {
    const [rule] = (await nextConfig.headers?.()) ?? [];
    const names = rule.headers.map((header) => header.key);

    expect(rule.source).toBe("/:path*");
    expect(names).toEqual(
      expect.arrayContaining([
        "Content-Security-Policy",
        "X-Frame-Options",
        "X-Content-Type-Options",
        "Referrer-Policy",
      ]),
    );
  });

  it("let the page talk only to its own server and never be framed", () => {
    const directives = CONTENT_SECURITY_POLICY.split("; ");

    expect(directives).toContain("default-src 'self'");
    expect(directives).toContain("connect-src 'self'");
    expect(directives).toContain("object-src 'none'");
    expect(directives).toContain("frame-ancestors 'none'");
    expect(CONTENT_SECURITY_POLICY).not.toContain("unsafe-eval"); // only in development
  });
});
