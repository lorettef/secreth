export type MembershipRole = "PLAYER" | "ADMIN" | "OWNER";
export type MembershipStatus = "ACTIVE" | "INACTIVE";
export type RegistrationState = "NOT_MEMBER" | "PROFILE_REQUIRED" | "REGISTERED";

export interface ClubMembership {
  id: string;
  name: string;
  membership_role: MembershipRole;
  membership_status: MembershipStatus;
}

export interface Profile {
  id: string;
  display_name: string;
  first_name: string | null;
  last_name: string | null;
  school_nickname: string | null;
}

export interface MeResponse {
  authenticated: boolean;
  telegram_id: number;
  registration_state: RegistrationState;
  club_id: string | null;
  membership_id: string | null;
  membership_role: MembershipRole | null;
  membership_status: MembershipStatus | null;
  profile: Profile | null;
}

export interface OnboardingResponse {
  registration_state: "REGISTERED";
  club_id: string;
  membership_id: string;
  membership_role: MembershipRole;
  membership_status: MembershipStatus;
  profile: Profile;
}

export interface ProfileInput {
  first_name: string;
  last_name: string;
  school_nickname: string;
}
