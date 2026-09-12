import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { StatusBar } from "expo-status-bar";

const DEFAULT_API_BASE = "http://10.197.73.197:8011";

const pageLabels = {
  page1: "CEO 일정",
  page2: "회사 주요 일정",
  page3: "KAI 주요 기사",
  page4: "경쟁사 동향",
  page5: "캘린더 설정",
  page6: "OpenAI 설정",
  page7: "APP 사용자",
};

const pageDescriptions = {
  page1: "CEO 일정과 주요 미팅을 확인합니다.",
  page2: "회사 주요 일정과 승인 흐름을 확인합니다.",
  page3: "승인된 KAI 관련 기사만 확인합니다.",
  page4: "승인된 경쟁사 동향만 확인합니다.",
  page5: "관리자 전용 설정 화면입니다.",
  page6: "관리자 전용 설정 화면입니다.",
  page7: "관리자 전용 사용자 관리 화면입니다.",
};

const roleMessages = {
  admin: "전체 운영과 설정을 관리합니다.",
  ceo: "핵심 일정과 승인된 기사만 빠르게 확인합니다.",
  staff: "업무 관련 일정과 승인된 기사만 확인합니다.",
};

const quickAccounts = [
  { label: "Admin", username: "admin", pin: "4000" },
  { label: "CEO", username: "ceo", pin: "2000" },
  { label: "Staff", username: "staff", pin: "3000" },
];

export default function App() {
  const [apiBase, setApiBase] = useState(DEFAULT_API_BASE);
  const [username, setUsername] = useState("ceo");
  const [pin, setPin] = useState("2000");
  const [session, setSession] = useState(null);
  const [activePage, setActivePage] = useState(null);
  const [pageData, setPageData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!session?.pages?.length) return;
    setActivePage((current) => current || session.pages[0].id);
  }, [session]);

  useEffect(() => {
    if (!session || !activePage) return;
    loadPage(activePage);
  }, [session, activePage]);

  async function request(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Request failed.");
    }
    return payload;
  }

  async function handleLogin() {
    setLoading(true);
    setNotice("");
    try {
      const payload = await request("/app-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), pin: pin.trim() }),
      });
      setSession(payload);
      setActivePage(payload.pages[0]?.id || null);
      setPageData(null);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadPage(pageId) {
    if (!session) return;
    setLoading(true);
    setNotice("");
    try {
      if (pageId === "page1" || pageId === "page2") {
        const payload = await request(`/calendar/${pageId}?role=${session.role}`);
        setPageData({ type: "calendar", payload });
      } else if (pageId === "page3" || pageId === "page4") {
        const feedType = pageId === "page3" ? "company" : "competitor";
        const payload = await request(`/feeds/${feedType}?role=${session.role}`);
        setPageData({ type: "feed", payload, feedType });
      } else {
        setPageData({ type: "restricted" });
      }
    } catch (error) {
      setPageData(null);
      setNotice(error.message);
    } finally {
      setLoading(false);
    }
  }

  function signOut() {
    setSession(null);
    setActivePage(null);
    setPageData(null);
    setNotice("");
  }

  function chooseAccount(account) {
    setUsername(account.username);
    setPin(account.pin);
  }

  if (!session) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="dark" />
        <ScrollView contentContainerStyle={styles.loginWrap}>
          <Text style={styles.eyebrow}>Connected App MVP</Text>
          <Text style={styles.appTitle}>CEO Briefing</Text>
          <Text style={styles.appSubtitle}>
            같은 화면 구조를 유지하고, 로그인한 역할에 따라 접근 가능한 페이지만 보여줍니다.
          </Text>

          <View style={styles.card}>
            <Text style={styles.sectionTitle}>빠른 로그인</Text>
            <View style={styles.quickRow}>
              {quickAccounts.map((account) => (
                <Pressable key={account.label} style={styles.quickChip} onPress={() => chooseAccount(account)}>
                  <Text style={styles.quickChipText}>{account.label}</Text>
                </Pressable>
              ))}
            </View>
            <Text style={styles.helper}>기본 계정: admin/4000, ceo/2000, staff/3000</Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.sectionTitle}>접속 정보</Text>
            <Text style={styles.label}>API Address</Text>
            <TextInput value={apiBase} onChangeText={setApiBase} autoCapitalize="none" style={styles.input} />
            <Text style={styles.label}>Username</Text>
            <TextInput value={username} onChangeText={setUsername} autoCapitalize="none" style={styles.input} />
            <Text style={styles.label}>PIN</Text>
            <TextInput value={pin} onChangeText={setPin} secureTextEntry keyboardType="number-pad" style={styles.input} />
            <Pressable style={styles.primaryButton} onPress={handleLogin} disabled={loading}>
              <Text style={styles.primaryButtonText}>{loading ? "로그인 중..." : "로그인"}</Text>
            </Pressable>
          </View>

          <View style={styles.card}>
            <Text style={styles.sectionTitle}>안내</Text>
            <Text style={styles.bodyText}>휴대폰과 PC는 같은 Wi-Fi에 연결되어 있어야 합니다.</Text>
            <Text style={styles.bodyText}>기본 API 주소는 현재 PC 내부 IP 기준으로 맞춰져 있습니다.</Text>
          </View>

          {!!notice && <Text style={styles.notice}>{notice}</Text>}
        </ScrollView>
      </SafeAreaView>
    );
  }

  const pageTitle = pageLabels[activePage] || activePage;
  const pageDescription = pageDescriptions[activePage] || "";

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <View style={styles.shell}>
        <View style={styles.header}>
          <View style={styles.headerText}>
            <Text style={styles.eyebrow}>{session.role.toUpperCase()} Access</Text>
            <Text style={styles.headerTitle}>{session.display_name}</Text>
            <Text style={styles.headerSubtitle}>{roleMessages[session.role] || ""}</Text>
          </View>
          <Pressable style={styles.secondaryButton} onPress={signOut}>
            <Text style={styles.secondaryButtonText}>로그아웃</Text>
          </Pressable>
        </View>

        <View style={styles.summaryCard}>
          <Text style={styles.summaryLabel}>현재 페이지</Text>
          <Text style={styles.summaryTitle}>{pageTitle}</Text>
          <Text style={styles.summaryBody}>{pageDescription}</Text>
        </View>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.navRow}>
          {session.pages.map((page) => (
            <Pressable
              key={page.id}
              style={[styles.navChip, activePage === page.id && styles.navChipActive]}
              onPress={() => setActivePage(page.id)}
            >
              <Text style={[styles.navChipText, activePage === page.id && styles.navChipTextActive]}>
                {pageLabels[page.id] || page.title}
              </Text>
            </Pressable>
          ))}
        </ScrollView>

        <View style={styles.contentHeader}>
          <View style={styles.contentHeaderText}>
            <Text style={styles.contentTitle}>{pageTitle}</Text>
            <Text style={styles.contentSubtitle}>{pageDescription}</Text>
          </View>
          <Pressable style={styles.secondaryButton} onPress={() => loadPage(activePage)}>
            <Text style={styles.secondaryButtonText}>새로고침</Text>
          </Pressable>
        </View>

        {!!notice && <Text style={styles.notice}>{notice}</Text>}

        {loading ? (
          <View style={styles.centerState}>
            <ActivityIndicator size="large" color="#1f5c71" />
          </View>
        ) : (
          <ScrollView contentContainerStyle={styles.pageBody}>
            {pageData?.type === "calendar" && <CalendarSection events={pageData.payload.events} />}
            {pageData?.type === "feed" && (
              <FeedSection
                published={pageData.payload.published}
                queued={pageData.payload.queued}
                canPublish={pageData.payload.can_publish}
                role={session.role}
              />
            )}
            {pageData?.type === "restricted" && (
              <View style={styles.card}>
                <Text style={styles.sectionTitle}>관리자 전용 화면</Text>
                <Text style={styles.bodyText}>현재 로그인 역할에서는 이 화면을 볼 수 없습니다.</Text>
              </View>
            )}
          </ScrollView>
        )}
      </View>
    </SafeAreaView>
  );
}

function CalendarSection({ events }) {
  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>일정</Text>
      {events?.length ? (
        events.map((event) => (
          <View key={event.id} style={styles.listItem}>
            <Text style={styles.itemTitle}>{event.title}</Text>
            <Text style={styles.bodyText}>
              {event.date} {event.time} - {event.place}
            </Text>
            <Text style={styles.metaText}>
              {event.owner} - {event.status}
            </Text>
          </View>
        ))
      ) : (
        <Text style={styles.emptyState}>표시할 일정이 없습니다.</Text>
      )}
    </View>
  );
}

function FeedSection({ published, queued, canPublish, role }) {
  const items = canPublish ? [...(queued || []), ...(published || [])] : published || [];
  const heading = canPublish ? "기사 큐 및 게시분" : "승인된 기사";
  const summaryLine =
    role === "admin"
      ? "관리자는 대기 기사와 게시 기사를 함께 확인합니다."
      : "앱 사용자는 승인된 기사만 확인합니다.";

  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>{heading}</Text>
      <Text style={styles.bodyText}>{summaryLine}</Text>
      {items.length ? (
        items.map((item) => (
          <View key={item.id} style={styles.listItem}>
            <Text style={styles.itemTitle}>{item.title}</Text>
            <Text style={styles.metaText}>{item.source}</Text>
            <Text style={styles.bodyText}>{item.summary}</Text>
          </View>
        ))
      ) : (
        <Text style={styles.emptyState}>표시할 기사가 없습니다.</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f3ecdf",
  },
  shell: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 18,
  },
  loginWrap: {
    padding: 24,
    gap: 16,
  },
  eyebrow: {
    color: "#b55f26",
    fontSize: 12,
    letterSpacing: 1.4,
    textTransform: "uppercase",
  },
  appTitle: {
    color: "#153a4b",
    fontSize: 34,
    fontWeight: "800",
    marginTop: 8,
  },
  appSubtitle: {
    color: "#375769",
    fontSize: 15,
    lineHeight: 22,
    marginTop: 8,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 12,
  },
  headerText: {
    flex: 1,
  },
  headerTitle: {
    color: "#153a4b",
    fontSize: 28,
    fontWeight: "800",
    marginTop: 6,
  },
  headerSubtitle: {
    color: "#4e6875",
    fontSize: 14,
    marginTop: 4,
    lineHeight: 20,
  },
  summaryCard: {
    backgroundColor: "#ffffff",
    borderRadius: 22,
    padding: 18,
    marginTop: 16,
  },
  summaryLabel: {
    color: "#b55f26",
    fontSize: 12,
    letterSpacing: 1.2,
    textTransform: "uppercase",
  },
  summaryTitle: {
    color: "#183744",
    fontSize: 22,
    fontWeight: "800",
    marginTop: 8,
  },
  summaryBody: {
    color: "#506875",
    fontSize: 14,
    marginTop: 6,
    lineHeight: 20,
  },
  navRow: {
    gap: 10,
    paddingVertical: 18,
  },
  navChip: {
    backgroundColor: "#d9e4e8",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 999,
  },
  navChipActive: {
    backgroundColor: "#1f5c71",
  },
  navChipText: {
    color: "#163847",
    fontWeight: "600",
  },
  navChipTextActive: {
    color: "#ffffff",
  },
  contentHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 14,
    gap: 10,
  },
  contentHeaderText: {
    flex: 1,
  },
  contentTitle: {
    color: "#182f3a",
    fontSize: 24,
    fontWeight: "800",
  },
  contentSubtitle: {
    color: "#5a6f79",
    fontSize: 14,
    marginTop: 4,
  },
  pageBody: {
    paddingBottom: 32,
    gap: 14,
  },
  card: {
    backgroundColor: "#ffffff",
    borderRadius: 22,
    padding: 18,
    shadowColor: "#000000",
    shadowOpacity: 0.06,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
    elevation: 3,
  },
  sectionTitle: {
    color: "#193743",
    fontSize: 18,
    fontWeight: "800",
    marginBottom: 12,
  },
  label: {
    color: "#274857",
    fontSize: 13,
    fontWeight: "700",
    marginTop: 6,
    marginBottom: 6,
  },
  input: {
    backgroundColor: "#eef3f5",
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: "#173846",
    fontSize: 15,
  },
  primaryButton: {
    backgroundColor: "#1f5c71",
    borderRadius: 14,
    alignItems: "center",
    paddingVertical: 13,
    marginTop: 12,
  },
  primaryButtonText: {
    color: "#ffffff",
    fontWeight: "700",
    fontSize: 15,
  },
  secondaryButton: {
    borderWidth: 1,
    borderColor: "#d8dde0",
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 10,
    backgroundColor: "#ffffff",
  },
  secondaryButtonText: {
    color: "#234556",
    fontWeight: "700",
  },
  quickRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 4,
  },
  quickChip: {
    backgroundColor: "#dfe9ec",
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  quickChipText: {
    color: "#183744",
    fontWeight: "700",
  },
  helper: {
    color: "#6a7c85",
    fontSize: 12,
    marginTop: 10,
  },
  notice: {
    color: "#b03e2f",
    fontSize: 13,
    lineHeight: 18,
  },
  centerState: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  listItem: {
    borderTopWidth: 1,
    borderTopColor: "#edf0f1",
    paddingTop: 12,
    marginTop: 12,
  },
  itemTitle: {
    color: "#183744",
    fontSize: 16,
    fontWeight: "700",
    marginBottom: 6,
  },
  metaText: {
    color: "#6b7b84",
    fontSize: 12,
    marginBottom: 6,
  },
  bodyText: {
    color: "#425c67",
    fontSize: 14,
    lineHeight: 20,
  },
  emptyState: {
    color: "#6a7c85",
    fontSize: 14,
    marginTop: 16,
  },
});
