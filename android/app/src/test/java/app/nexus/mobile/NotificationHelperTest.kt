package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationHelperTest {

    @Test
    fun `answer notification ids are stable and do not collide for known hash collisions`() {
        val first = NotificationHelper.answerNotificationId("FB")
        val second = NotificationHelper.answerNotificationId("Ea")

        assertEquals(first, NotificationHelper.answerNotificationId("FB"))
        assertNotEquals(first, second)
    }

    @Test
    fun `transfer notification ids are stable and do not collide`() {
        val first = NotificationHelper.transferNotificationId("session", "FB")
        val second = NotificationHelper.transferNotificationId("session", "Ea")

        assertEquals(first, NotificationHelper.transferNotificationId("session", "FB"))
        assertNotEquals(first, second)
    }

    @Test
    fun `transfer notification ids include session and file identity`() {
        assertNotEquals(
            NotificationHelper.transferNotificationId("session-a", "file"),
            NotificationHelper.transferNotificationId("session-b", "file")
        )
        assertNotEquals(
            NotificationHelper.transferNotificationId("session-a", "file-a"),
            NotificationHelper.transferNotificationId("session-a", "file-b")
        )
    }

    @Test
    fun `transfer notification ids include Hermes profile identity`() {
        assertNotEquals(
            NotificationHelper.transferNotificationId("shared-session", "file", "default"),
            NotificationHelper.transferNotificationId("shared-session", "file", "work")
        )
    }

    @Test
    fun `notification ids use explicit stable positive type ranges`() {
        val answer = NotificationHelper.answerNotificationId("session-x")
        val foreground = NotificationHelper.answerForegroundNotificationId()
        val transfer = NotificationHelper.transferNotificationId("session-x", "file-y")

        assertTrue(answer in 0x20000000..0x3fffffff)
        assertTrue(transfer in 0x40000000..0x5fffffff)
        assertEquals(0x60000000, foreground)
        assertTrue(answer > 0 && transfer > 0 && foreground > 0)
    }

    @Test
    fun `download cancellation id includes the state session id`() {
        val state = FileDownloadState(
            key = "file-y",
            status = DownloadStatus.DOWNLOADING,
            sessionId = "session-x",
            profileId = "work"
        )
        val target = downloadTransferNotificationTarget(state)

        assertEquals("file-y", target.key)
        assertEquals("session-x", target.sessionId)
        assertEquals("work", target.profileId)
        assertEquals(
            NotificationHelper.transferNotificationId("session-x", "file-y", "work"),
            downloadTransferNotificationId(state)
        )
        assertNotEquals(
            NotificationHelper.transferNotificationId("session-x", "file-y", "default"),
            downloadTransferNotificationId(state)
        )
    }

    @Test
    fun `pending intent request codes are distinct per type and key`() {
        val answerCode = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.ANSWER,
            sessionId = "session-abc",
            fileKey = null
        )
        val transferCode = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.TRANSFER,
            sessionId = "session-abc",
            fileKey = "file-xyz"
        )

        assertNotEquals(answerCode, transferCode)
        assertEquals(
            answerCode,
            NotificationHelper.pendingIntentRequestCode(
                type = NotificationHelper.PendingIntentType.ANSWER,
                sessionId = "session-abc",
                fileKey = null
            )
        )
    }

    @Test
    fun `same type and same keys produce identical pending intent codes`() {
        val a = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.TRANSFER,
            sessionId = "s1",
            fileKey = "f1"
        )
        val b = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.TRANSFER,
            sessionId = "s1",
            fileKey = "f1"
        )
        assertEquals(a, b)
    }

    @Test
    fun `pending intent codes resist known Java hash collisions`() {
        assertNotEquals(
            NotificationHelper.pendingIntentRequestCode(NotificationHelper.PendingIntentType.TRANSFER, "session", "FB"),
            NotificationHelper.pendingIntentRequestCode(NotificationHelper.PendingIntentType.TRANSFER, "session", "Ea")
        )
    }

    @Test
    fun `pending intent data uniquely includes type session and file`() {
        val first = NotificationHelper.pendingIntentData(NotificationHelper.PendingIntentType.TRANSFER, "FB", "file")
        val second = NotificationHelper.pendingIntentData(NotificationHelper.PendingIntentType.TRANSFER, "Ea", "file")
        val answer = NotificationHelper.pendingIntentData(NotificationHelper.PendingIntentType.ANSWER, "FB", "file")

        assertNotEquals(first, second)
        assertNotEquals(first, answer)
        assertEquals(first, NotificationHelper.pendingIntentData(NotificationHelper.PendingIntentType.TRANSFER, "FB", "file"))
    }

    @Test
    fun `pending intent identity includes Hermes profile`() {
        val defaultCode = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.ANSWER,
            sessionId = "shared-session",
            fileKey = null,
            profileId = "default"
        )
        val workCode = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.ANSWER,
            sessionId = "shared-session",
            fileKey = null,
            profileId = "work"
        )
        val defaultData = NotificationHelper.pendingIntentData(
            NotificationHelper.PendingIntentType.ANSWER,
            "shared-session",
            null,
            "default"
        )
        val workData = NotificationHelper.pendingIntentData(
            NotificationHelper.PendingIntentType.ANSWER,
            "shared-session",
            null,
            "work"
        )

        assertNotEquals(defaultCode, workCode)
        assertNotEquals(defaultData, workData)
        assertEquals(
            workCode,
            NotificationHelper.pendingIntentRequestCode(
                NotificationHelper.PendingIntentType.ANSWER,
                "shared-session",
                null,
                "work"
            )
        )
    }

    @Test
    fun `answer notification ids can be scoped by Hermes profile`() {
        val defaultKey = profileScopedStorageKey("default", "shared-session")
        val workKey = profileScopedStorageKey("work", "shared-session")

        assertNotEquals(
            NotificationHelper.answerNotificationId(defaultKey),
            NotificationHelper.answerNotificationId(workKey)
        )
    }

}
