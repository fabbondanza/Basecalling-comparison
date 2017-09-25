#!/usr/bin/env python3
"""
This script is a Nanopolish wrapper I wrote for use on my SLURM-managed cluster. It does the read
alignment, launches Nanopolish jobs, waits for them to finish and merges them together. If any
parts of the assembly fail in Nanopolish it replaces them with Ns so the merge can complete.
"""

import sys
import os
import subprocess
import time
import shutil


def main():
    assembly_filename = os.path.abspath(sys.argv[1])
    read_filename = os.path.abspath(sys.argv[2])
    raw_fast5_dir = os.path.abspath(sys.argv[3])
    output_dir = os.path.abspath(sys.argv[4])
    nanopolish_dir = os.path.abspath(sys.argv[5])
    threads = int(sys.argv[6])

    nanopolish_exec = os.path.join(nanopolish_dir, 'nanopolish')
    nanopolish_makerange = os.path.join(nanopolish_dir, 'scripts', 'nanopolish_makerange.py')
    nanopolish_merge = os.path.join(nanopolish_dir, 'scripts', 'nanopolish_merge.py')

    set_name = assembly_filename.split('/')[-1].split('.fasta')[0]
    print('\nPreparing to run Nanopolish for ' + set_name)

    pid = str(os.getpid())
    temp_dir = os.path.join(output_dir, pid + '_temp_dir')
    os.mkdir(temp_dir)
    print('Moving into ' + temp_dir)
    os.chdir(temp_dir)

    print('Getting ranges: ', end='')
    polish_ranges = get_nanopolish_ranges(nanopolish_makerange, assembly_filename)
    print(', '.join(polish_ranges))

    # Align reads with minimap2
    print('Aligning reads')
    alignment_command = ('minimap2 -x map10k -a -t ' + str(threads) + ' ' + 
                         assembly_filename + ' ' + read_filename + 
                         ' | samtools sort -o reads.sorted.bam -T reads.tmp -')
    subprocess.run(alignment_command, shell=True, check=True)
    subprocess.run('samtools index reads.sorted.bam', shell=True, check=True)

    # Run Nanopolish index on reads
    print('Running nanopolish index:')
    index_command = nanopolish_exec + ' index -d ' + raw_fast5_dir + ' ' + read_filename
    subprocess.run(index_command, shell=True, check=True)

    # Run Nanopolish variants on ranges
    print('Launching SLURM jobs:', flush=True)
    job_prefix = 'Nanopolish_' + pid + '_'
    for polish_range in polish_ranges:
        job_name = job_prefix + polish_range
        variants_command = nanopolish_exec + ' variants --consensus polished.' + polish_range + '.fa -w ' + polish_range + ' -r ' + read_filename + ' -b reads.sorted.bam -g ' + assembly_filename + ' -t 2 --min-candidate-frequency 0.1'
        sbatch_command = 'sbatch -p sysgen --nodes=1 --job-name=' + job_name + ' --ntasks=1 --cpus-per-task=2 --mem=4096 --time=0-4:0:00 --wrap "' + variants_command + '"'
        print(sbatch_command)
        subprocess.run(sbatch_command, shell=True, check=True)

    # Wait for jobs to finish
    start_time = time.time()
    while True:
        time.sleep(60)
        remaining_jobs = get_remaining_nanopolish_jobs(job_prefix)
        if remaining_jobs == 0:
            print('All Nanopolish jobs are done!')
            break
        elapsed_time = str(int(round(time.time() - start_time)))
        print('Waiting for Nanopolish jobs to finish... (' + elapsed_time + ' sec elapsed, ' + str(remaining_jobs) + ' jobs remaining)', flush=True)

    # Make empty ranges, if necessary
    incomplete_ranges = [x for x in polish_ranges if not os.path.isfile('polished.' + x + '.fa')]
    if incomplete_ranges:
        print('WARNING: some ranges did not complete: ' + ', '.join(incomplete_ranges))
    for incomplete_range in incomplete_ranges:
        fasta_filename = 'polished.' + incomplete_range + '.fa'
        start = int(incomplete_range.split(':')[-1].split('-')[0])
        end = int(incomplete_range.split('-')[-1])
        range_size = end - start
        with open(fasta_filename, 'wt') as fasta:
            fasta.write('>')
            fasta.write(incomplete_range)
            fasta.write('\n')
            fasta.write('N' * range_size)
            fasta.write('\n')

    # Merge results together
    final_assembly = '../' + set_name + '.fasta'
    merge_command = 'python ' + nanopolish_merge + ' polished.*.fa > ' + final_assembly
    subprocess.run(merge_command, shell=True, check=True)

    os.chdir('..')
    shutil.rmtree(temp_dir)


def get_nanopolish_ranges(nanopolish_makerange, assembly_filename):
    command = 'python ' + nanopolish_makerange + ' ' + assembly_filename
    range_out = subprocess.check_output(command, shell=True).splitlines()
    return [x.decode() for x in range_out]


def get_remaining_nanopolish_jobs(job_prefix):
    current_jobs = subprocess.check_output('squeue -o "%.70j %.8i %.10T"', shell=True).decode().splitlines()
    remaining_jobs = [x for x in current_jobs if job_prefix in x]
    return len(remaining_jobs)


if __name__ == '__main__':
    main()