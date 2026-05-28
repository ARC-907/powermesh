"""Tests for PSU efficiency model and sensor data classes."""

from src.sensors import (
    estimate_psu_efficiency,
    CpuReading,
    GpuReading,
    SystemReading,
    PowerSnapshot,
)


class TestPsuEfficiency:
    def test_bronze_at_50pct_load(self):
        eta = estimate_psu_efficiency(325, psu_wattage=650, psu_rating="bronze")
        assert 0.87 <= eta <= 0.89

    def test_gold_at_50pct_load(self):
        eta = estimate_psu_efficiency(325, psu_wattage=650, psu_rating="gold")
        assert 0.91 <= eta <= 0.93

    def test_very_low_load(self):
        eta = estimate_psu_efficiency(30, psu_wattage=650, psu_rating="bronze")
        # At ~4.6% load, should return lowest curve point
        assert 0.80 <= eta <= 0.82

    def test_full_load(self):
        eta = estimate_psu_efficiency(650, psu_wattage=650, psu_rating="bronze")
        assert 0.84 <= eta <= 0.86

    def test_zero_psu_wattage_returns_default(self):
        eta = estimate_psu_efficiency(100, psu_wattage=0)
        assert eta == 0.85

    def test_unknown_rating_uses_bronze(self):
        eta_unknown = estimate_psu_efficiency(325, psu_wattage=650, psu_rating="unknown")
        eta_bronze = estimate_psu_efficiency(325, psu_wattage=650, psu_rating="bronze")
        assert eta_unknown == eta_bronze


class TestPowerSnapshot:
    def test_total_gpu_power(self):
        snap = PowerSnapshot(
            gpus=[
                GpuReading(index=0, power_w=150),
                GpuReading(index=1, power_w=200),
            ]
        )
        assert snap.total_gpu_power_w == 350

    def test_avg_gpu_util(self):
        snap = PowerSnapshot(
            gpus=[
                GpuReading(utilization_pct=40),
                GpuReading(utilization_pct=80),
            ]
        )
        assert snap.avg_gpu_util == 60.0

    def test_avg_gpu_temp(self):
        snap = PowerSnapshot(
            gpus=[
                GpuReading(temperature_c=60),
                GpuReading(temperature_c=70),
            ]
        )
        assert snap.avg_gpu_temp == 65.0

    def test_no_gpus_returns_zero(self):
        snap = PowerSnapshot()
        assert snap.total_gpu_power_w == 0
        assert snap.avg_gpu_util == 0
        assert snap.avg_gpu_temp == 0

    def test_total_vram(self):
        snap = PowerSnapshot(
            gpus=[
                GpuReading(vram_used_mb=4096),
                GpuReading(vram_used_mb=8192),
            ]
        )
        assert snap.total_vram_used_mb == 12288
