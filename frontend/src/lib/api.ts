import type {
  ClubMembership,
  MeResponse,
  OnboardingResponse,
  ProfileInput,
} from "../models";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
  ) {
    super(code);
  }
}

export class ApiClient {
  constructor(
    private readonly initData: string,
    private readonly baseUrl = "",
  ) {}

  async myClubs(): Promise<{ clubs: ClubMembership[]; blocked: boolean }> {
    return this.request<{ clubs: ClubMembership[]; blocked: boolean }>(
      "/api/v1/my-clubs",
    );
  }

  me(clubId: string): Promise<MeResponse> {
    return this.request(`/api/v1/me?club_id=${encodeURIComponent(clubId)}`);
  }

  register(inviteToken: string, profile: ProfileInput): Promise<OnboardingResponse> {
    return this.request("/api/v1/onboarding/register", {
      method: "POST",
      body: JSON.stringify({ invite_token: inviteToken, ...profile }),
    });
  }

  completeProfile(
    clubId: string,
    profile: ProfileInput,
  ): Promise<OnboardingResponse> {
    return this.request("/api/v1/onboarding/profile", {
      method: "POST",
      body: JSON.stringify({ club_id: clubId, ...profile }),
    });
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...options,
        headers: {
          Authorization: `tma ${this.initData}`,
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...options.headers,
        },
      });
    } catch {
      throw new ApiError(0, "NETWORK_ERROR");
    }
    if (!response.ok) {
      let code = "API_ERROR";
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === "string") code = body.detail;
      } catch {
        // A neutral error is safer than exposing an unexpected server body.
      }
      throw new ApiError(response.status, code);
    }
    return (await response.json()) as T;
  }
}
