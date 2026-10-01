# 0002 React Native for mobile app and enterprise SDK

Status: Accepted, 2026-10-01 (supersedes Flutter in original specification)

## Context
PEOCIT engineering team has React and TypeScript experience. Native calling work (CallKit, PushKit, ConnectionService, Live Caller ID Lookup) is required with either framework.

## Decision
React Native (New Architecture) with Expo development builds. TypeScript shared across mobile, portal and SDK through workspace packages (`packages/domain`, `packages/api-client`, `packages/i18n`). Native calling code lives in Swift and Kotlin TurboModules under `apps/mobile/modules`.

## Consequences
One language end to end and a larger hiring pool. Arabic RTL switching needs an app reload, so language is chosen at onboarding and RTL layouts get dedicated visual regression tests. At least one engineer must own Swift for iOS calling.
