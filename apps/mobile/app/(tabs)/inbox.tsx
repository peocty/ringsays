import type { Folder, IntentDisplay } from "@ringsays/client";
import { useInfiniteQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FlatList, Pressable, RefreshControl, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { Banner, Body, Button, Card, IntentSummary, Loading, Segmented, urgentStyle } from "../../src/components/ui";
import { useLang } from "../../src/i18n";
import { errorMessage } from "../../src/lib/errors";
import { keys } from "../../src/lib/keys";
import { getSession } from "../../src/lib/session";
import { space, useTheme } from "../../src/theme";

export default function Inbox() {
  const { t } = useTranslation();
  const { rtl } = useLang();
  const th = useTheme();
  const [folder, setFolder] = useState<Folder>("REQUESTS");
  const q = useInfiniteQuery({
    queryKey: keys.inbox(folder),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => (await getSession()).inbox(folder, pageParam),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: 30_000,
  });
  const items: IntentDisplay[] = q.data?.pages.flatMap((p) => p.items) ?? [];
  const empty = { REQUESTS: t("inbox.emptyRequests"), SCHEDULED: t("inbox.emptyScheduled"), HISTORY: t("inbox.emptyHistory") }[folder];
  return (
    <SafeAreaView edges={["left", "right"]} style={{ flex: 1, backgroundColor: th.bg, direction: rtl ? "rtl" : "ltr" }}>
      <View style={{ padding: space.lg, paddingBottom: 0 }}>
        <Segmented
          value={folder}
          onChange={setFolder}
          options={[
            { id: "REQUESTS", label: t("inbox.requests") },
            { id: "SCHEDULED", label: t("inbox.scheduled") },
            { id: "HISTORY", label: t("inbox.history") },
          ]}
        />
      </View>
      {q.isPending ? <Loading /> : null}
      {q.error ? (
        <View style={{ padding: space.lg, gap: space.sm }}>
          <Banner tone="error">{errorMessage(q.error, t)}</Banner>
          <Button title={t("app.retry")} onPress={() => void q.refetch()} />
        </View>
      ) : null}
      <FlatList
        testID="inbox-list"
        data={items}
        keyExtractor={(i) => i.intent_id}
        contentContainerStyle={{ padding: space.lg, gap: space.md }}
        refreshControl={<RefreshControl refreshing={q.isRefetching && !q.isFetchingNextPage} onRefresh={() => void q.refetch()} />}
        ListEmptyComponent={q.data ? <Body muted>{empty}</Body> : null}
        onEndReached={() => q.hasNextPage && !q.isFetchingNextPage && void q.fetchNextPage()}
        renderItem={({ item }) => (
          <Pressable
            accessibilityRole="button"
            testID={`intent-${item.intent_id}`}
            onPress={() => router.push(`/intent/${item.intent_id}`)}
          >
            <Card style={urgentStyle(item, th)}>
              <IntentSummary intent={item} />
            </Card>
          </Pressable>
        )}
      />
    </SafeAreaView>
  );
}
