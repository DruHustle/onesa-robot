import {expect} from '@playwright/test';

// The UI verifies readiness for at most 20s before its bounded 10s login request.
// Use this only after submitting Sign in; ordinary layout assertions keep their defaults.
export async function waitForOperatorSignIn(page) {
 await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible({timeout:30000});
}
