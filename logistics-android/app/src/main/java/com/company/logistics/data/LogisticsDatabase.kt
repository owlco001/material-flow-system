package com.company.logistics.data

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

/**
 * 离线操作队列的本地持久化。
 *
 * 契约约束（第 5 节）：
 *  - 所有写操作携带 clientOperationId 作为幂等键；
 *  - 幂等键服务端保存至少 7 天；
 *  - 服务端时间为审计唯一依据，本地时间仅作展示参考。
 *
 * 因此本地记录必须保存幂等键与实际 payload，联网后原样重放，
 * 由服务端依据幂等键去重，保证「离线暂存 → 联网同步」不产生重复单据。
 */
@Entity(tableName = "offline_operations")
data class OfflineOperationEntity(
    @PrimaryKey val id: String,
    /** 幂等键，重放时原样回传 */
    val clientOperationId: String,
    val materialCode: String,
    val operationType: String,
    /** 同步状态：PENDING / SYNCING / SYNCED / FAILED / CONFLICT */
    val status: String,
    /** 本地创建时间（仅展示参考） */
    val createdAt: Long,
    // ---- 重放所需 payload ----
    val quantity: Int = 0,
    val targetLocation: String? = null,
    val materialId: String? = null,
    val expectedInventoryVersion: Int? = null,
    val remark: String? = null,
    /** 服务端时间戳（同步成功后回填） */
    val serverTime: String? = null,
    /** 失败原因，供用户判断是否重试 */
    val errorMessage: String? = null,
    /** 已自动重试次数（达到上限后转 FAILED，等人工处理） */
    val retryCount: Int = 0,
    /** 下次允许自动重试的时间戳（毫秒）；null=立即 */
    val nextRetryAt: Long? = null
)

@Dao
interface OfflineOperationDao {
    @Query("SELECT * FROM offline_operations ORDER BY createdAt DESC")
    fun observeAll(): Flow<List<OfflineOperationEntity>>

    /**
     * 待重放队列：只取 PENDING。
     *
     * FAILED 是终态（自动重试已用尽或数据损坏），必须由用户在「待处理」里手动重试/放弃，
     * 否则每次同步都会把同一批坏记录再打一遍服务端（旧实现就是 PENDING+FAILED 一起查）。
     * nextRetryAt 的到期判断放在 Repository 里做，保持 DAO 签名稳定。
     */
    @Query("SELECT * FROM offline_operations WHERE status = 'PENDING' ORDER BY createdAt ASC LIMIT 20")
    suspend fun pending(): List<OfflineOperationEntity>

    /**
     * Due records are filtered before LIMIT so older items in backoff cannot starve later due work.
     * createdBeforeMillis bounds one sync snapshot; new queue rows are picked up next run. The default
     * fallback preserves simple JVM fakes but sees only their legacy pending() page; Room executes SQL.
     */
    @Query("SELECT * FROM offline_operations WHERE status = 'PENDING' AND (nextRetryAt IS NULL OR nextRetryAt <= :nowMillis) AND createdAt <= :createdBeforeMillis ORDER BY createdAt ASC LIMIT 20")
    suspend fun pendingDue(nowMillis: Long, createdBeforeMillis: Long): List<OfflineOperationEntity> = pending()
        .filter {
            (it.nextRetryAt == null || it.nextRetryAt <= nowMillis) && it.createdAt <= createdBeforeMillis
        }
        .take(20)

    @Query("SELECT EXISTS(SELECT 1 FROM offline_operations WHERE status = 'PENDING')")
    suspend fun hasPending(): Boolean = pending().any { it.status == "PENDING" }

    @Query("SELECT COUNT(*) FROM offline_operations WHERE status IN ('PENDING','FAILED','SYNCING')")
    fun observePendingCount(): Flow<Int>

    /** 需要人工处理的记录数：重放失败或与服务端冲突（冲突不会自动重试，必须提示用户）。 */
    @Query("SELECT COUNT(*) FROM offline_operations WHERE status IN ('FAILED','CONFLICT')")
    fun observeAttentionCount(): Flow<Int>

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(operation: OfflineOperationEntity): Long

    @Query("UPDATE offline_operations SET status = :status, errorMessage = :error WHERE id = :id")
    suspend fun updateStatus(id: String, status: String, error: String? = null)

    /** 自动重试调度：写入重试计数与下次重试时间（status 传 PENDING 或 FAILED）。 */
    @Query("UPDATE offline_operations SET status = :status, errorMessage = :error, " +
           "retryCount = :retryCount, nextRetryAt = :nextRetryAt WHERE id = :id")
    suspend fun scheduleRetry(id: String, status: String, error: String?, retryCount: Int, nextRetryAt: Long?) {
        // 仅测试替身会命中默认实现；Room 会为 @Query 生成真正实现。
    }

    @Query("UPDATE offline_operations SET status = :status, serverTime = :serverTime, errorMessage = NULL WHERE id = :id")
    suspend fun markSynced(id: String, status: String, serverTime: String?)

    @Query("DELETE FROM offline_operations WHERE status = 'SYNCED'")
    suspend fun clearSynced()

    @Query("SELECT * FROM offline_operations WHERE id = :id")
    suspend fun findById(id: String): OfflineOperationEntity?

    /**
     * 恢复上次进程被杀时卡在 SYNCING 的记录：改回 PENDING 等待重放。
     * 不恢复的话，observePendingCount 会一直把它们算进待同步数，但 pending() 永远查不到。
     */
    @Query("UPDATE offline_operations SET status = 'PENDING', errorMessage = NULL WHERE status = 'SYNCING'")
    suspend fun resetStuckSyncing(): Int

    /** 失败/冲突记录：重试 → 改回 PENDING 并清空错误原因，等待下次重放。 */
    @Query("UPDATE offline_operations SET status = 'PENDING', errorMessage = NULL WHERE id = :id")
    suspend fun resetToPending(id: String)

    /** Release only this cancelled claim; production overrides this fallback with an atomic CAS. */
    suspend fun restoreCancelledClaim(id: String) {
        if (findById(id)?.status == "SYNCING") resetToPending(id)
    }

    /** 失败/冲突记录：放弃 → 直接删除，不再重放。 */
    @Query("DELETE FROM offline_operations WHERE id = :id")
    suspend fun deleteById(id: String)
}

/**
 * Room skips SQL generation for interface methods with bodies. Keep test-friendly defaults on
 * OfflineOperationDao, but redeclare production queries abstract so KSP must generate their SQL.
 */
@Dao
interface RoomOfflineOperationDao : OfflineOperationDao {
    @Query("UPDATE offline_operations SET status = 'PENDING', errorMessage = NULL WHERE id = :id AND status = 'SYNCING'")
    override suspend fun restoreCancelledClaim(id: String)

    @Query("SELECT * FROM offline_operations WHERE status = 'PENDING' AND (nextRetryAt IS NULL OR nextRetryAt <= :nowMillis) AND createdAt <= :createdBeforeMillis ORDER BY createdAt ASC LIMIT 20")
    override suspend fun pendingDue(nowMillis: Long, createdBeforeMillis: Long): List<OfflineOperationEntity>

    @Query("SELECT EXISTS(SELECT 1 FROM offline_operations WHERE status = 'PENDING')")
    override suspend fun hasPending(): Boolean

    @Query("UPDATE offline_operations SET status = :status, errorMessage = :error, " +
           "retryCount = :retryCount, nextRetryAt = :nextRetryAt WHERE id = :id")
    override suspend fun scheduleRetry(id: String, status: String, error: String?, retryCount: Int, nextRetryAt: Long?)
}

@Database(entities = [OfflineOperationEntity::class], version = 3, exportSchema = false)
abstract class LogisticsDatabase : RoomDatabase() {
    abstract fun offlineOperationDao(): RoomOfflineOperationDao

    companion object {
        /** 2 → 3：离线队列增加自动重试计数与下次重试时间（0.5.45）。 */
        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE offline_operations ADD COLUMN retryCount INTEGER NOT NULL DEFAULT 0")
                db.execSQL("ALTER TABLE offline_operations ADD COLUMN nextRetryAt INTEGER")
            }
        }
    }
}
