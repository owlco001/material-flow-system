package com.company.logistics.data

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase
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
    val errorMessage: String? = null
)

@Dao
interface OfflineOperationDao {
    @Query("SELECT * FROM offline_operations ORDER BY createdAt DESC")
    fun observeAll(): Flow<List<OfflineOperationEntity>>

    @Query("SELECT * FROM offline_operations WHERE status IN ('PENDING','FAILED') ORDER BY createdAt ASC")
    suspend fun pending(): List<OfflineOperationEntity>

    @Query("SELECT COUNT(*) FROM offline_operations WHERE status IN ('PENDING','FAILED','SYNCING')")
    fun observePendingCount(): Flow<Int>

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(operation: OfflineOperationEntity): Long

    @Query("UPDATE offline_operations SET status = :status, errorMessage = :error WHERE id = :id")
    suspend fun updateStatus(id: String, status: String, error: String? = null)

    @Query("UPDATE offline_operations SET status = :status, serverTime = :serverTime, errorMessage = NULL WHERE id = :id")
    suspend fun markSynced(id: String, status: String, serverTime: String?)

    @Query("DELETE FROM offline_operations WHERE status = 'SYNCED'")
    suspend fun clearSynced()

    @Query("SELECT * FROM offline_operations WHERE id = :id")
    suspend fun findById(id: String): OfflineOperationEntity?
}

@Database(entities = [OfflineOperationEntity::class], version = 2, exportSchema = false)
abstract class LogisticsDatabase : RoomDatabase() {
    abstract fun offlineOperationDao(): OfflineOperationDao
}
