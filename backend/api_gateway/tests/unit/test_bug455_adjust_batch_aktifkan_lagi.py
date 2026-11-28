"""BUG-455: adjust batch naik pada batch 'depleted' mengaktifkannya lagi (FEFO hanya membaca status active)."""
import inspect

import app.routers.item_batches as IB


def test_adjust_batch_mengaktifkan_kembali_batch_habis():
    src = inspect.getsource(IB.adjust_batch_quantity)
    assert "SET status = 'active'" in src and "status = 'depleted'" in src
    assert src.index("SET status = 'depleted'") < src.index("SET status = 'active'")   # habis dinilai dulu, lalu aktif lagi bila qty > 0
    assert "current_quantity > 0" in src and "expiry_date >= $3" in src
