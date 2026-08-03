import os
import sys

# Asigurăm că ierarhia de foldere este recunoscută
directory = os.path.dirname(os.path.abspath("__file__"))
sys.path.insert(0, os.path.dirname(directory))

from ibex_cpu.shared_types import CoverageDatabase
from ibex_cpu.instructions import Instr, Encoding


class InstructionMonitor:
    def __init__(self, dut):
        self.clk = dut.clk_i
        # Folosim semnalele RVFI (RISC-V Formal Interface) - sunt cele mai precise
        self.insn_valid = dut.u_top.rvfi_valid
        self.insn_pc = dut.u_top.rvfi_pc_rdata
        self.insn = dut.u_top.rvfi_insn

        self.coverage_db = CoverageDatabase(instructions={}, cross_coverage={})
        self.last_pc = None
        self.last_insn = None
        self.last_insn_obj = None  # Stocăm obiectul pentru cross-coverage rapid

        # Inițializăm baza de date pentru toate instrucțiunile cunoscute
        for instr in Instr:
            self.coverage_db.instructions[instr] = {
                cov: 0 for cov in instr.type().coverpoints()
            }
            self.coverage_db.cross_coverage[instr] = {
                (other_instr, cov): 0
                for (other_instr, cov) in instr.type().cross_coverpoints()
            }

    def sample_insn_coverage(self):
        # REGULA 1: Dacă semnalul valid e 0, procesorul nu a terminat nicio instrucțiune
        if self.insn_valid.value == 0:
            return

        current_pc = int(self.insn_pc.value)
        current_insn_raw = int(self.insn.value)

        # REGULA 2: Dacă PC-ul este același ca data trecută, înseamnă că suntem 
        # în același ciclu de execuție. Nu numărăm instrucțiunea de două ori!
        if self.last_pc == current_pc:
            return

        # Încercăm să decodăm instrucțiunea
        insn_obj = Encoding(current_insn_raw).typed()

        if insn_obj is not None:
            try:
                mnemonic = insn_obj.instruction()

                # Înregistrăm coverage-ul funcțional (Seen, etc.)
                for coverpoint in insn_obj.sample_coverage():
                    self.coverage_db.instructions[mnemonic][coverpoint] += 1

                # Înregistrăm cross-coverage-ul (ex: RAW Hazard cu instrucțiunea anterioară)
                if self.last_insn_obj is not None:
                    for cross_cov in insn_obj.sample_cross_coverage(self.last_insn_obj):
                        self.coverage_db.cross_coverage[mnemonic][cross_cov] += 1

                # Salvăm instrucțiunea curentă pentru a fi "anterioara" la următorul pas
                self.last_insn_obj = insn_obj

            except AssertionError:
                # Dacă e o instrucțiune validă RISC-V dar necunoscută de noi (ex: Bitmanip),
                # o ignorăm tăcut. Nu printăm nimic pentru a nu încetini simularea.
                pass

        # Actualizăm PC-ul pentru a evita dubla numărare la următorul ciclu de ceas
        self.last_pc = current_pc
        self.last_insn = current_insn_raw