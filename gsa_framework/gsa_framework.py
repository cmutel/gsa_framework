import plotly.graph_objects as go
import os
from utils import read_hdf5_array, write_hdf5_array

# Local files
from sampling.get_samples import *
from sensitivity_analysis.correlation_coefficients import  correlation_coefficients
from sensitivity_analysis.sobol_indices import sobol_indices
from sensitivity_analysis.extended_FAST import  eFAST_indices
from sensitivity_analysis.gradient_boosting import xgboost_scores
from sensitivity_analysis.dissimilarity_measures import dissimilarity_measure

# Sampler and Global Sensitivity Analysis (GSA) mapping dictionaries
sampler_mapping = {
    'saltelli': saltelli_samples,
    'sobol':    sobol_samples,
    'eFAST':    eFAST_samples,
    'random':   random_samples,
    'custom':   custom_samples,
    'dissimilarity_samples': dissimilarity_samples
}
interpreter_mapping = {
    'correlation_coefficients': correlation_coefficients,
    'sobol_indices': sobol_indices,
    'eFAST_indices': eFAST_indices,
    'xgboost': xgboost_scores,
    'dissimilarity_measure': dissimilarity_measure,
}


class Problem:
    """Definition of a global sensitivity analysis problem.

    Parameters
    ----------
    sampler : str
        The correct function for generating samples of specific type is retrieved from ``sampler_mapping``.
    model : class
        Can include any model that contain methods:
            ``__num_input_params__`` that outputs number of model input parameters;
            ``__rescale__(X)``       that rescales samples from standard uniform to appropriate distributions;
            ``__call__(X_rescaled)`` that computes model outputs for all samples in ``X_rescaled``.
        Models can be run locally or remotely (TODO).
    interpreter : str
        The correct function for GSA is retrieved from ``interpreter_mapping``.
    write_dir : str
        Directory where intermediate results and plots will be stored.
    iterations : int
        Number of Monte Carlo iterations.
    seed : int
        Random seed.
    X : np.array of size [iterations, num_params]
        Custom parameter sampling matrix in standard uniform [0,1] range.

    Raises
    ------
        Errors?

    """
    def __init__(self, sampler, model, interpreter, write_dir, iterations=None, seed=None, X=None):
        # General
        self.seed = seed
        self.model = model
        self.num_params = self.model.__num_input_params__()
        self.iterations = iterations or self.guess_iterations()
        self.write_dir = write_dir
        # Save some useful info in a GSA dictionary
        self.gsa_dict = {
            'iterations': self.iterations,
            'num_params': self.num_params,
            'write_dir': self.write_dir,
        }
        # Create necessary directories
        self.make_dirs()
        # Sampling strategy depends on the interpreter
        self.interpreter_str = interpreter
        self.interpreter_fnc = interpreter_mapping.get(self.interpreter_str)
        self.sampler_str = sampler
        # Generate samples
        self.gsa_dict.update(
            {
                'sampler_str': self.sampler_str,
                'X': X,
            }
        )
        self.gsa_dict.update({'X': self.generate_samples()})
        self.gsa_dict.update({'X_rescaled': self.rescale_samples()})
        # Run model
        self.gsa_dict.update({'y': self.run_locally()})
        # Compute GSA indices
        self.gsa_dict.update({'sa_results': self.interpret()})

    def make_dirs(self):
        """Create subdirectories where intermediate results will be stored."""
        dirs_list = [
            'arrays'
        ]
        for dir in dirs_list:
            dir_path = os.path.join(self.write_dir, dir)
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)

    def guess_iterations(self, CONSTANT=10):
        """Function that computes number of Monte Carlo iterations depending on the GSA method.

        Returns
        -------
        num_iterations : int
            Number of iterations that should be sufficient for the convergence of GSA indices. For many GSA methods
            this number depends on the desired confidence level and confidence interval width.

        """

        if self.interpreter == 'correlation_coefficients':
            corrcoef_constants = get_corrcoef_num_iterations()
            num_iterations = np.max(
                corrcoef_constants['pearson']['num_iterations'],
                corrcoef_constants['spearman']['num_iterations'],
                corrcoef_constants['kendall']['num_iterations']
            )
            return num_iterations
        else:
            return self.num_params * CONSTANT

    def generate_samples(self, X=None):
        """Use ``self.sampler`` to generate normalized samples for this problem.

        Returns
        -------
        filename_X : str
            Path where parameter sampling matrix ``X`` for standard uniform samples is stored.

        TODO Chris, this function is horrible.

        """

        self.base_sampler_str = 'no_base'
        if self.interpreter_str == 'sobol_indices':
            print('Changing samples to saltelli, because of faster indices convergence')
            self.sampler_str = 'saltelli'
            self.seed = None
        elif self.interpreter_str == 'eFAST_indices':
            print('Changing samples to eFAST, because of faster indices convergence')
            self.sampler_str = 'eFAST'
        elif self.interpreter_str == 'dissimilarity_measure':
            print('Samples should be adapted for dissimilarity sensitivity measure')
            if self.sampler_str in sampler_mapping.keys():
                self.base_sampler_str = self.sampler_str
            else:
                self.base_sampler_str = 'random'
            self.base_sampler_fnc = sampler_mapping.get(self.base_sampler_str)
            self.sampler_str = 'dissimilarity_samples'
            self.gsa_dict.update({'base_sampler_str': self.base_sampler_str})
            self.gsa_dict.update({'base_sampler_fnc': self.base_sampler_fnc})
        else:
            if X != None:
                self.sampler_str = 'custom'
                self.seed = None
        self.sampler_fnc = sampler_mapping.get(self.sampler_str, 'random')
        self.gsa_dict.update({'sampler_fnc': self.sampler_fnc})
        self.gsa_dict.update({'seed': self.seed})

        self.filename_X = os.path.join(
            self.write_dir,
            'arrays',
            'X_' + self.sampler_str + '_' + self.base_sampler_str + \
            '_iterations_' + str(self.iterations) + \
            '_num_params_' + str(self.num_params) + \
            '_seed_' + str(self.seed) + '.hdf5',
        )
        if not os.path.exists(self.filename_X):
            X = self.sampler_fnc(self.gsa_dict)
            write_hdf5_array(X, self.filename_X)
        return self.filename_X

    def rescale_samples(self):
        """Rescale samples from standard uniform to appropriate distributions and write ``X_rescaled`` to a file.

        Returns
        -------
        filename_X_rescaled : str
            Path where parameter sampling matrix ``X_rescaled`` for samples from appropriate distributions is stored.

        """
        path_start = os.path.split(self.filename_X)[0]
        path_end = os.path.split(self.filename_X)[-1]
        self.filename_X_rescaled = os.path.join(path_start, 'X_rescaled' + path_end[1:])
        if not os.path.exists(self.filename_X_rescaled):
            X = read_hdf5_array(self.filename_X)
            X_rescaled = self.model.__rescale__(X)
            write_hdf5_array(X_rescaled, self.filename_X_rescaled)
        return self.filename_X_rescaled

    def run_locally(self):
        """Obtain ``model`` outputs from the ``X_rescaled`` parameter sampling matrix.

        Run Monte Carlo simulations and write results to a file.

        Returns
        -------
        filename_y : str
            Path where model outputs ``y`` are stored.

        """

        path_start = os.path.split(self.filename_X)[0]
        path_end = os.path.split(self.filename_X)[-1]
        self.filename_y = os.path.join(path_start, 'y' + path_end[1:])
        if not os.path.exists(self.filename_y):
            X_rescaled = read_hdf5_array(self.filename_X_rescaled)
            y = self.model(X_rescaled)
            write_hdf5_array(y, self.filename_y)
        return self.filename_y

    def run_remotely(self):
        """Prepare files for remote execution.

        Dispatch could be via a cloud API, dask, multiprocessing, etc.

        This function needs to create evaluation batches, e.g. 100 Monte Carlo iterations. TODO

        """

        pass

    def interpret(self):
        """Computation of GSA indices.

        Returns
        -------
        gsa_indices_dict : dict
            Keys are GSA indices names, values - sensitivity indices for all parameters.

        """
        y = read_hdf5_array(self.filename_y)
        X_rescaled = read_hdf5_array(self.filename_X_rescaled)
        self.gsa_dict.update({'y': y.flatten()})
        self.gsa_dict.update({'X': X_rescaled})
        gsa_indices_dict = self.interpreter_fnc(self.gsa_dict)
        return gsa_indices_dict

    # def validate_gsa(self): TODO


    def plot_sa_results(self, sa_indices, influential_inputs=[], filename=''):
        """Simplistic plotting of GSA results of GSA indices vs parameters. Figure is saved in the ``write_dir``.

        Parameters
        ----------
        sa_indices : dict
            Keys are GSA indices names, values - sensitivity indices for all parameters.
        influential_inputs : list
            Parameters that are known to be influential, eg if the model is analytical. Ground truth for GSA validation.
        filename : str
            Filename for saving the plot, otherwise it will be saved under ``sensitivity_plot.pdf``.

        """

        index_name = list(sa_indices.keys())[0]
        index_vals = list(sa_indices.values())[0]

        sa_indices_influential = np.array([index_vals[f] for f in influential_inputs])

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x = np.arange(len(index_vals)),
                y = index_vals,
                mode = 'markers',
                marker = dict(
                    color = 'blue'
                ),
                name='All parameters',
            ),
        )
        if len(influential_inputs)>0:
            fig.add_trace(
                go.Scatter(
                    x = influential_inputs,
                    y = sa_indices_influential,
                    mode = 'markers',
                    marker = dict(
                        color = 'red'
                    ),
                name = 'Known influential parameters',
                ),
            )
        fig.update_layout(
            xaxis_title = "Model parameters",
            yaxis_title = index_name,
        )
        if not filename:
            filename = 'sensitivity_plot.pdf'
        pathname = os.path.join(self.write_dir, filename)
        fig.write_image(pathname)

