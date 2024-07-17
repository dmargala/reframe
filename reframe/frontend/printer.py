# Copyright 2016-2024 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import os
import shutil
import time
import traceback
from tabulate import tabulate

import reframe.core.logging as logging
import reframe.core.runtime as rt
import reframe.utility.color as color
from reframe.core.exceptions import SanityError
from reframe.frontend.reporting import format_testcase_from_json
from reframe.utility import nodelist_abbrev


class PrettyPrinter:
    '''Pretty printing facility for the framework.

    It takes care of formatting the progress output and adds some more
    cosmetics to specific levels of messages, such as warnings and errors.

    It also takes care of formatting and printing the various reports.

    The actual printing is delegated to an internal logger, which is
    responsible for printing.
    '''

    def __init__(self):
        self.colorize = True
        self.line_width = 78
        self.status_width = 10

    def reset_progress(self, total_cases):
        self._progress_count = 0
        self._progress_total = total_cases

    def separator(self, linestyle, msg=''):
        if linestyle == 'short double line':
            line = self.status_width * '='
        elif linestyle == 'short single line':
            line = self.status_width * '-'
        else:
            raise ValueError('unknown line style')

        self.info('[%s] %s' % (line, msg))

    def status(self, status, message='', just=None, level=logging.INFO):
        if just == 'center':
            status = status.center(self.status_width - 2)
        elif just == 'right':
            status = status.rjust(self.status_width - 2)
        else:
            status = status.ljust(self.status_width - 2)

        status_stripped = status.strip()
        if self.colorize:
            if status_stripped in ('ABORT', 'ABORTED', 'DRY', 'SKIP'):
                status = color.colorize(status, color.YELLOW)
            elif status_stripped in ('FAIL', 'FAILED', 'ERROR'):
                status = color.colorize(status, color.RED)
            else:
                status = color.colorize(status, color.GREEN)

        final_msg = f'[ {status} ] '
        if status_stripped in ('ABORT', 'OK', 'SKIP', 'FAIL'):
            if self._progress_count < self._progress_total:
                self._progress_count += 1

            width = len(str(self._progress_total))
            padded_progress = str(self._progress_count).rjust(width)
            final_msg += f'({padded_progress}/{self._progress_total}) '

        final_msg += message
        logging.getlogger().log(level, final_msg)

    def timestamp(self, msg='', separator=None):
        msg = f'{msg} {time.strftime("%c%z")}'
        if separator:
            self.separator(separator, msg)
        else:
            self.info(msg)

    def __getattr__(self, attr):
        # delegate all other attribute lookup to the underlying logger
        return getattr(logging.getlogger(), attr)

    def __setattr__(self, attr, value):
        # Delegate colorize setting to the backend logger
        if attr == 'colorize':
            logging.getlogger().colorize = value
            self.__dict__['colorize'] = value
        else:
            super().__setattr__(attr, value)

    def failure_report(self, report, rerun_info=True, global_stats=False):
        '''Print a failure report'''

        def _head_n(filename, prefix, num_lines=10):
            # filename and prefix are `None` before setup
            if filename is None or prefix is None:
                return []

            try:
                with open(os.path.join(prefix, filename)) as fp:
                    lines = [
                        f'--- {filename} (first {num_lines} lines) ---'
                    ]
                    for i, line in enumerate(fp):
                        if i < num_lines:
                            # Remove trailing '\n'
                            lines.append(line.rstrip())

                lines += [f'--- {filename} ---']
            except OSError as e:
                lines = [f'--- {filename} ({e}) ---']

            return lines

        def _print_failure_info(rec, runid, total_runs):
            self.info(line_width * '-')
            self.info(f"FAILURE INFO for {rec['name']} "
                      f"(run: {runid}/{total_runs})")
            self.info(f"  * Description: {rec['descr']}")
            self.info(f"  * System partition: {rec['system']}")
            self.info(f"  * Environment: {rec['environ']}")
            self.info(f"  * Stage directory: {rec['stagedir']}")
            self.info(f"  * Node list: "
                      f"{nodelist_abbrev(rec['job_nodelist'])}")
            job_type = 'local' if rec['scheduler'] == 'local' else 'batch job'
            self.info(f"  * Job type: {job_type} (id={rec['jobid']})")
            self.info(f"  * Dependencies (conceptual): "
                      f"{rec['dependencies_conceptual']}")
            self.info(f"  * Dependencies (actual): "
                      f"{rec['dependencies_actual']}")
            self.info(f"  * Maintainers: {rec['maintainers']}")
            self.info(f"  * Failing phase: {rec['fail_phase']}")
            if rerun_info and not rec['fixture']:
                self.info(f"  * Rerun with '-n /{rec['hashcode']}"
                          f" -p {rec['environ']} --system "
                          f"{rec['system']} -r'")

            msg = rec['fail_reason']
            if isinstance(rec['fail_info']['exc_value'], SanityError):
                lines = [msg]
                lines += _head_n(rec['job_stdout'], prefix=rec['stagedir'])
                lines += _head_n(rec['job_stderr'], prefix=rec['stagedir'])
                msg = '\n'.join(lines)

            self.info(f"  * Reason: {msg}")

            tb = ''.join(traceback.format_exception(
                *rec['fail_info'].values()))
            if rec['fail_severe']:
                self.info(tb)
            else:
                self.verbose(tb)

        line_width = min(80, shutil.get_terminal_size()[0])
        self.info(' SUMMARY OF FAILURES '.center(line_width, '='))

        for run_no, run_info in enumerate(report['runs'], start=1):
            if not global_stats and run_no != len(report['runs']):
                continue

            for r in run_info['testcases']:
                if r['result'] in {'pass', 'abort', 'skip'}:
                    continue

                _print_failure_info(r, run_no, len(report['runs']))

        self.info(line_width * '-')

    def failure_stats(self, report, global_stats=False):
        current_run = rt.runtime().current_run
        failures = {}
        for runid, run_data in enumerate(report['runs']):
            if not global_stats and runid != current_run:
                continue

            for tc in run_data['testcases']:
                info = f'{tc["display_name"]}'
                info += f' @{tc["system"]}:{tc["partition"]}+{tc["environ"]}'

                failed_stage = tc['fail_phase']
                failures.setdefault(failed_stage, [])
                failures[failed_stage].append(info)

        line_width = shutil.get_terminal_size()[0]
        stats_start = line_width * '='
        stats_title = 'FAILURE STATISTICS'
        stats_end = line_width * '-'
        stats_body = []
        row_format = "{:<13} {:<5} {}"
        stats_hline = row_format.format(13*'-', 5*'-', 60*'-')
        stats_header = row_format.format('Phase', '#', 'Failing test cases')
        if global_stats:
            num_tests = report['session_info']['num_cases']
        else:
            num_tests = report['runs'][current_run]['num_cases']

        num_failures = 0
        for fl in failures.values():
            num_failures += len(fl)

        stats_body = ['']
        stats_body.append(f'Total number of test cases: {num_tests}')
        stats_body.append(f'Total number of failures: {num_failures}')
        stats_body.append('')
        stats_body.append(stats_header)
        stats_body.append(stats_hline)
        for p, l in failures.items():
            stats_body.append(row_format.format(p, len(l), l[0]))
            for f in l[1:]:
                stats_body.append(row_format.format('', '', str(f)))

        if stats_body:
            for line in (stats_start, stats_title, *stats_body, stats_end):
                self.info(line)

    def retry_report(self, report):
        '''Print a report for test retries'''

        if not rt.runtime().current_run:
            # Do nothing if no retries
            return

        line_width = shutil.get_terminal_size()[0]
        lines = [line_width * '=']
        lines.append('SUMMARY OF RETRIES')
        lines.append(line_width * '-')
        messages = {}
        for i, run in enumerate(report['runs'][1:], start=1):
            for tc in run['testcases']:
                # Overwrite entry from previous run if available
                tc_info = format_testcase_from_json(tc)
                messages[tc_info] = (
                    f"  * Test {tc_info} was retried {i} time(s) and"
                    f" {'failed' if tc['result'] == 'fail' else 'passed'}."
                )

        for msg in sorted(messages):
            lines.append(msg)

        self.info('\n'.join(lines))

    def performance_report(self, data, **kwargs):
        width = min(80, shutil.get_terminal_size()[0])
        self.info('')
        self.info(' PERFORMANCE REPORT '.center(width, '='))
        self.info('')
        self.table(data, **kwargs)
        self.info('')

    def table(self, data, **kwargs):
        '''Print tabular data'''

        kwargs.setdefault('headers', 'firstrow')
        kwargs.setdefault('tablefmt', 'mixed_grid')
        self.info(tabulate(data, **kwargs))
