package com.company.logistics.data

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.RoomDatabase
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "offline_operations")
data class OfflineOperationEntity(
    @androidx.room.PrimaryKey val id: String,
    val clientOperationId: String,
    val materialCode: String,
    val operationType: String,
    val status: String,
    val createdAt: Long
)

@Dao
interface OfflineOperationDao {
    @Query("SELECT * FROM offline_operations ORDER BY createdAt DESC")
    fun observeAll(): Flow<List<OfflineOperationEntity>>

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(operation: OfflineOperationEntity): Long

    @Query("UPDATE offline_operations SET status = :status WHERE id = :id")
    suspend fun updateStatus(id: String, status: String)
}

@Database(entities = [OfflineOperationEntity::class], version = 1, exportSchema = false)
abstract class LogisticsDatabase : RoomDatabase() {
    abstract fun offlineOperationDao(): OfflineOperationDao
}
